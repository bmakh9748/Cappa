"""The neural half of detection: PaddleOCR's pretrained text-detection model
(DBNet, PP-OCRv5 mobile). Given a frame, it returns tight boxes around every
piece of text — any language, any styling, no background box needed, trained
on millions of samples. It replaces hand-tuned "does this look like text"
heuristics; every text line it finds becomes a live, hoverable caption.

The model runs through onnxruntime — RapidOCR ships the same PP-OCRv5 weights
pre-converted to ONNX — instead of PaddlePaddle's executor. Same model, same
boxes, ~7x faster (measured ~55 vs ~375 ms on the same synthetic frame;
real 1920px captures cost ~112 ms on CPU at the CPU working size. Paddle
can't use its oneDNN fast path on Windows without crashing, and its plain
executor is that much slower). On a machine
with a GPU the session goes to DirectML (gpu.py decides) and the working
size grows: the scan gets sharper AND no slower — see TARGET_SIDE_GPU. The
worker still calls scan() only when the frame actually changed, throttled —
never per-frame. The frame is shrunk before inference (cost is roughly
quadratic in side length; captions are big and survive shrinking) and box
coordinates are scaled back to full resolution.

The model loads lazily (~1 s; RapidOCR downloads the ~5 MB .onnx once ever);
if loading fails, scan() returns [] and the app keeps running without caption
detection rather than crashing."""

import sys

from . import gpu

TARGET_SIDE = 736    # CPU working size: shrink the long side to this before
                     # inference. Sized for the worst common case: on a full
                     # 1920x1080 browser capture, 736 still finds ~26px
                     # captions (measured: 480 finds NOTHING there, 640
                     # misses small ones); smaller captures (popouts,
                     # selected areas) shrink less or not at all and scan
                     # proportionally faster.
TARGET_SIDE_GPU = 1280  # GPU working size. Measured (GTX 1660, DirectML):
                     # 104 ms/scan vs the CPU path's 112 ms at 736 — and on
                     # a dense 1920px browser frame it boxes 55 text lines
                     # where 736 boxes 8. The CPU-era shrink was the quality
                     # ceiling, not the model; the GPU can afford to keep
                     # the small text. (1280 over 960: 16 ms dearer, 55
                     # lines vs 34 on that same frame.)
MIN_SCORE = 0.6      # detection confidence gate (the model's box_thresh)
MERGE_GAP = 0.9      # of line height: max horizontal gap between fragments
                     # of the same text line (big/spaced/italic styles come
                     # back from DBNet as several boxes)
MERGE_OVERLAP = 0.6  # of the smaller height: vertical overlap = "same line"


def merge_lines(boxes):
    """One box per TEXT LINE: DBNet returns big/spaced/italic captions as
    several fragments (per word or word-group). Fragments that overlap
    vertically and sit within a glyph-height's gap horizontally are the
    same line; everything downstream (ledger, watcher, OCR) wants them
    as one."""
    merged = []
    for box in sorted(boxes, key=lambda b: b[0]):
        for i, m in enumerate(merged):
            if _same_line(m, box):
                merged[i] = (min(m[0], box[0]), min(m[1], box[1]),
                             max(m[2], box[2]), max(m[3], box[3]))
                break
        else:
            merged.append(box)
    return merged


def _same_line(a, b):
    ah, bh = a[3] - a[1], b[3] - b[1]
    overlap = min(a[3], b[3]) - max(a[1], b[1])
    if overlap < MERGE_OVERLAP * max(min(ah, bh), 1):
        return False
    gap = max(a[0], b[0]) - min(a[2], b[2])  # negative when they overlap
    return gap <= MERGE_GAP * max(ah, bh)


class TextDetector:
    def __init__(self, target_side=None, min_score=MIN_SCORE):
        # None -> sized to the device: the GPU affords a bigger working
        # size at lower latency than the CPU manages at 736 (measured, see
        # TARGET_SIDE_GPU). The install probe only picks the INTENTION;
        # _ensure() re-checks where the session actually landed and drops
        # back to CPU sizing if DirectML wasn't usable after all (VM,
        # broken driver). An explicit target_side always wins and is
        # never second-guessed.
        self._auto_sized = target_side is None
        if target_side is None:
            target_side = TARGET_SIDE_GPU if gpu.available() else TARGET_SIDE
        self._target_side = target_side
        self._min_score = min_score
        self._model = None
        self._device = None
        self._failed = False
        self._fast_pre = False  # our preprocess only replaces the wrapper's
                                # when the pack's mean/std match its formula
        self._scan_errors = set()  # runtime failure kinds reported, once each

    def warm(self):
        """Load the model now (worker calls this at thread start, so the
        first caption never pays the load time) — and push one dummy frame
        through it: a session's first DirectML run pays a one-time graph
        compile (~1-2 s measured) that belongs here, not on the first
        caption. scan() is fail-open, so a failing warm scan reports
        itself there and the app keeps running."""
        self._ensure()
        if self._model is None:
            return
        import numpy as np
        self.scan(np.zeros((90, 160, 4), dtype=np.uint8))

    @property
    def ready(self):
        return self._model is not None

    @property
    def device(self):
        """'gpu'/'cpu' once ready (None = not loaded, or placement
        unreadable) — the worker prints it and picks the scan cadence from
        it, so a silent fall-back to CPU is visible AND acted on."""
        return self._device

    def _say_scan_failed(self, exc):
        """One stderr line per failure kind: a structural failure repeats
        on every scan, and one line is signal where a stream is noise
        (same discipline as ocr.py's _say_read_failed)."""
        kind = type(exc).__name__
        if kind in self._scan_errors:
            return
        self._scan_errors.add(kind)
        print("cappa: text detection scan failed (%s: %s) — no captions "
              "will be found until it recovers" % (kind, exc),
              file=sys.stderr)

    def scan(self, frame):
        """frame: (H, W, 4) BGRA uint8. Returns [(l, t, r, b), ...] text
        boxes in full-resolution frame coordinates."""
        self._ensure()
        if self._model is None:
            return []
        import cv2
        import numpy as np

        h, w = frame.shape[:2]
        scale = min(1.0, self._target_side / max(h, w, 1))
        try:
            if self._fast_pre:
                # _detect resizes once, straight to the /32 grid, and its
                # postprocess maps boxes straight back to full resolution.
                polys = self._detect(frame, scale, cv2, np)
                scale = 1.0
            else:  # a pack with unexpected mean/std: the wrapper knows best
                if scale < 1.0:  # shrink BGRA first: 4 small copies beat
                    frame = cv2.resize(frame, None, fx=scale, fy=scale,
                                       interpolation=cv2.INTER_AREA)
                img = np.ascontiguousarray(frame[:, :, :3])
                result = self._model(img, use_det=True, use_cls=False,
                                     use_rec=False)
                polys = () if result.boxes is None else result.boxes
        except Exception as exc:
            # A session that LOADED can still fail at RUNTIME on DirectML —
            # driver reset (TDR) while a game runs, VRAM exhaustion on a
            # shared card — failure modes the CPU path never had. No boxes
            # is no evidence: the app keeps running, and the failure says
            # so once per kind instead of killing the capture thread.
            self._say_scan_failed(exc)
            return []
        boxes = []
        for poly in polys:
            xs = [float(p[0]) / scale for p in poly]
            ys = [float(p[1]) / scale for p in poly]
            boxes.append((int(min(xs)), int(min(ys)),
                          int(max(xs)), int(max(ys))))
        return merge_lines(boxes)

    def _detect(self, frame, scale, cv2, np):
        """One det pass running rapidocr's det module directly — its ONNX
        session and DBNet postprocess — with OUR pre/post plumbing. Why:
        the wrapper's DetPreProcess normalises in float64 and recopies
        twice, ~32 ms of CPU per scan at the GPU working size (measured),
        for a tensor this produces byte-identically in ~7 ms —
        (x/255 - .5)/.5 is x*(2/255)-1, and PP-OCR det packs all use
        mean = std = 0.5 (checked at load; scan() falls back to the
        wrapper otherwise). One fused resize replaces the old
        shrink-then-round pair, and the postprocess is handed the FULL
        frame shape so it maps boxes straight back to full resolution —
        no second coordinate pass. Box parity with the wrapper verified
        on real captures (0.0 px drift, pre-fuse; box counts identical
        after). `frame` is BGRA; the alpha plane is split off free
        rather than sliced off with a copy."""
        det = self._model.text_det
        h, w = frame.shape[:2]
        rh = int(round(h * scale / 32) * 32)
        rw = int(round(w * scale / 32) * 32)
        if rh <= 0 or rw <= 0:
            return ()
        if not frame.flags["C_CONTIGUOUS"]:
            frame = np.ascontiguousarray(frame)
        if (rh, rw) != (h, w):
            interp = (cv2.INTER_AREA if rh < h or rw < w
                      else cv2.INTER_LINEAR)
            frame = cv2.resize(frame, (rw, rh), interpolation=interp)
        tensor = np.empty((1, 3, rh, rw), dtype=np.float32)
        for c, plane in enumerate(cv2.split(frame)[:3]):
            np.multiply(plane, np.float32(2.0 / 255.0), out=tensor[0, c],
                        casting="unsafe")
        tensor -= 1.0
        preds = det.session(tensor)
        boxes, _scores = det.postprocess_op(preds, (h, w))
        return () if boxes is None else boxes

    def _ensure(self):
        if self._model is not None or self._failed:
            return
        try:
            from rapidocr import EngineType, ModelType, OCRVersion, RapidOCR
            self._model = RapidOCR(params={
                "Global.use_cls": False,
                "Global.use_rec": False,
                "Det.engine_type": EngineType.ONNXRUNTIME,
                "Det.ocr_version": OCRVersion.PPOCRV5,
                "Det.model_type": ModelType.MOBILE,
                "Det.box_thresh": self._min_score,
                # scan() already shrinks; these stop the wrapper resizing
                # AGAIN (its default limit_type 'min' UPSCALES small frames,
                # which costs 6x and helped nothing when measured).
                "Det.limit_side_len": self._target_side,
                "Det.limit_type": "max",
                # GPU when the venv has it; rapidocr falls back to CPU on
                # its own when the provider list disagrees (fail-open).
                "EngineConfig.onnxruntime.use_dml": gpu.available(),
            })
            self._device = gpu.session_device(self._model.text_det)
            # _detect()'s fused normalise assumes mean = std = 0.5 (what
            # every PP-OCR det pack ships). Check the loaded pack once; a
            # mismatch just keeps the wrapper path, never wrong boxes.
            mean = self._model.text_det.mean or [0.5] * 3
            std = self._model.text_det.std or [0.5] * 3
            self._fast_pre = (list(mean) == [0.5] * 3
                              and list(std) == [0.5] * 3)
            if self._auto_sized and self._device == "cpu" \
                    and self._target_side != TARGET_SIDE:
                # The DML wheel is installed but the session LANDED on CPU
                # (no usable adapter: VM, remote session, broken driver).
                # GPU sizing on a CPU session would be the worst of both
                # (~360 ms scans, measured) — drop to the CPU working size
                # and say so. (scan() pre-shrinks below the baked-in
                # limit_side_len, so re-sizing after load is safe.)
                self._target_side = TARGET_SIDE
                print("cappa: GPU build present but sessions run on the "
                      "cpu — using the CPU working size", file=sys.stderr)
            import logging
            # After the import, which sets the level itself: otherwise
            # RapidOCR WARNs on every scan that finds no text — which is
            # most rescans of a caption-free scene.
            logging.getLogger("RapidOCR").setLevel(logging.ERROR)
        except Exception as exc:  # missing package / download failure
            self._failed = True
            print("cappa: text detection unavailable (%s: %s) — captions "
                  "will not be found" % (type(exc).__name__, exc),
                  file=sys.stderr)
