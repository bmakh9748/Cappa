"""Step 4, second half: READ the text inside boxes the detector accepted.

Recognition runs the multi-script PP-OCR rec model (rapidocr's default,
PP-OCRv6 small) through onnxruntime — the same stack as detector.py. One
model reads Japanese, Chinese, English and more, no language setting needed:
measured against the v5-ch and japan-specific packs on rendered caption
lines, the default was the most accurate on BOTH Japanese and English and
the fastest (~20 ms/line).

Cost discipline: the worker calls read() only for boxes that just passed the
geometric classifier — a few times a minute, never per scan — so the
detection path gets no slower.

Fail-open by design: if the model can't load or a read errors, read()
returns (None, 0.0), and callers must treat unreadable text as NO EVIDENCE,
never as junk. Captions in scripts the model can't read worked before OCR
landed and must keep working."""

import sys

import numpy as np

PAD = 4  # px of context around the crop; rec likes a little breathing room


class TextReader:
    def __init__(self):
        self._model = None
        self._failed = False

    def warm(self):
        """Load the model now (worker calls this at thread start)."""
        self._ensure()

    @property
    def ready(self):
        return self._model is not None

    def read(self, frame, box):
        """frame: (H, W, 4) BGRA uint8, FULL resolution (recognition wants
        the sharpest crop we have). box: (l, t, r, b) full-res px.
        Returns (text, confidence); (None, 0.0) when reading is unavailable."""
        self._ensure()
        if self._model is None:
            return None, 0.0
        h, w = frame.shape[:2]
        l, t, r, b = box
        if b - t < 2 or r - l < 2:  # judged BEFORE padding: a sliver of a
            return None, 0.0        # box is no evidence, however padded
        t, b = max(t - PAD, 0), min(b + PAD, h)
        l, r = max(l - PAD, 0), min(r + PAD, w)
        crop = np.ascontiguousarray(frame[t:b, l:r, :3])
        try:
            res = self._model(crop, use_det=False, use_cls=False,
                              use_rec=True)
        except Exception:
            return None, 0.0
        if not res.txts:
            return "", 0.0
        score = float(res.scores[0]) if res.scores else 0.0
        return res.txts[0], score

    def _ensure(self):
        if self._model is not None or self._failed:
            return
        try:
            from rapidocr import EngineType, RapidOCR
            self._model = RapidOCR(params={
                "Global.use_det": False,
                "Global.use_cls": False,
                # Deliberately no Rec.lang_type/ocr_version: the default
                # multi-script model beat the per-language packs (see module
                # docstring). Captions are horizontal, so no cls either.
                "Rec.engine_type": EngineType.ONNXRUNTIME,
            })
            import logging
            logging.getLogger("RapidOCR").setLevel(logging.ERROR)
        except Exception as exc:  # missing package / download failure
            self._failed = True
            print("cappa: text reading unavailable (%s: %s) — detection "
                  "runs without text rules" % (type(exc).__name__, exc),
                  file=sys.stderr)
