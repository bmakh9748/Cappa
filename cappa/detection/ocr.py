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

from .sentence import Sentence

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

        Returns (sentence, confidence): a Sentence whose Words are the
        clickable hotspot units, boxes in FRAME coordinates. For spaced
        scripts a Word is a word; for CJK it is the model's script-run
        grouping (kanji block / kana run) until real tokenisation. When
        word geometry isn't available the whole line is one Word; when
        reading is unavailable entirely: (None, 0.0)."""
        self._ensure()
        if self._model is None:
            return None, 0.0
        h, w = frame.shape[:2]
        l, t, r, b = box
        if b - t < 2 or r - l < 2:  # judged BEFORE padding: a sliver of a
            return None, 0.0        # box is no evidence, however padded
        ct, cb = max(t - PAD, 0), min(b + PAD, h)
        cl, cr = max(l - PAD, 0), min(r + PAD, w)
        crop = np.ascontiguousarray(frame[ct:cb, cl:cr, :3])
        try:
            res = self._model(crop, use_det=False, use_cls=False,
                              use_rec=True, return_word_box=True)
        except Exception:
            return None, 0.0
        if not res.txts:
            return Sentence("", box, []), 0.0
        text = res.txts[0]
        score = float(res.scores[0]) if res.scores else 0.0
        spans = self._word_spans(res, cl, cr, t, b)
        if not spans and text:
            spans = [(text, box)]  # no geometry: the line is one hotspot
        return Sentence(text, box, spans), score

    @staticmethod
    def _word_spans(res, cl, cr, t, b):
        """Partition the line into word spans at the MIDPOINTS between
        adjacent words' edge-character columns. The recogniser emits each
        character at one CTC column, but the emission point drifts within
        the glyph — fixed margins around it made boxes slide into the gaps
        between words. Midpoints instead tile the whole line: every pixel
        belongs to exactly one word, so a hover can never land 'between'
        words or on a shifted box."""
        info = res.word_results[0] if res.word_results else None
        if info is None or not getattr(info, "line_txt_len", 0):
            return []
        unit = (cr - cl) / float(info.line_txt_len)
        groups = [(chars, cols) for chars, cols
                  in zip(info.words, info.word_cols) if chars and cols]
        if not groups:
            return []
        # first/last character centre of each word, in crop-x px
        centres = [((cols[0] + 0.5) * unit, (cols[-1] + 0.5) * unit)
                   for _, cols in groups]
        # The line's OUTER edges come from the character pitch, not the
        # crop: a det box wider than the text must not hand the first/last
        # word a hotspot over empty background.
        deltas = [(cols[i + 1] - cols[i]) * unit
                  for _, cols in groups for i in range(len(cols) - 1)]
        pitch = sorted(deltas)[len(deltas) // 2] if deltas else (b - t)
        line_l = max(0.0, centres[0][0] - 0.7 * pitch)
        line_r = min(float(cr - cl), centres[-1][1] + 0.7 * pitch)
        spans = []
        for i, (chars, _cols) in enumerate(groups):
            left = line_l if i == 0 else \
                (centres[i - 1][1] + centres[i][0]) / 2
            right = line_r if i == len(groups) - 1 else \
                (centres[i][1] + centres[i + 1][0]) / 2
            spans.append(("".join(chars),
                          (int(cl + left), t, int(cl + right), b)))
        return spans

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
