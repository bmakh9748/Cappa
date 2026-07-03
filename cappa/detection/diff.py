"""Step 2 — frame diff: decide when a frame is worth OCRing.

Compares each captured frame to the previous one on a downscaled copy,
per-channel (a grey-mean diff is blind to colour flips of equal brightness,
e.g. red -> blue). A subtitle appearing looks like: quiet → a burst of change
(the fade/pop-in) → quiet again. OCR wants the *settled* frame, so `feed()`
returns True exactly once per burst — on the first frame that has stayed quiet
for `settle_frames` ticks after a change — never on every fade-in frame and
never while the region is static.

After each feed() the instantaneous results are exposed for the next stage
(regions.py tracks caption candidates across them): `mask` is the per-pixel
changed/quiet bool array on the downscaled grid (None on the first frame after
a reset or a resize), `sample` the downscaled BGR int16 frame it came from.

All tuning knobs live in the constructor defaults below (see PLAN.md's "Diff
sensitivity" notes). No Qt in here: this is a plain, testable unit."""

import numpy as np

DOWNSCALE = 4          # sample every Nth pixel per axis (N*N fewer pixels)
PIXEL_THRESHOLD = 18   # channel delta (0-255) for one pixel to count as changed
CHANGED_FRACTION = 0.004  # fraction of sampled pixels that must change
SETTLE_FRAMES = 3      # consecutive quiet frames before a change "settles"


class FrameDiff:
    def __init__(self, downscale=DOWNSCALE, pixel_threshold=PIXEL_THRESHOLD,
                 changed_fraction=CHANGED_FRACTION, settle_frames=SETTLE_FRAMES):
        self._downscale = downscale
        self._pixel_threshold = pixel_threshold
        self._changed_fraction = changed_fraction
        self._settle_frames = settle_frames
        self._prev = None      # downscaled BGR of the previous frame
        self._pending = False  # saw a change, waiting for it to settle
        self._quiet = 0        # consecutive quiet frames while pending
        self.sample = None     # downscaled BGR of the latest frame
        self.mask = None       # bool: which sampled pixels changed this frame

    def feed(self, frame):
        """frame: (H, W, 4) BGRA uint8. True => changed-and-settled: run the
        detection stages on this frame now."""
        cur = self._sample(frame)
        prev, self._prev = self._prev, cur
        self.sample = cur

        if prev is None or prev.shape != cur.shape:
            # First frame after a reset, or the tracked region was resized:
            # treat everything as changed and wait for it to settle.
            self.mask = None
            self._pending, self._quiet = True, 0
            return False

        delta = np.abs(cur - prev).max(axis=2)  # strongest channel per pixel
        self.mask = delta > self._pixel_threshold
        changed = np.count_nonzero(self.mask)
        if changed >= self._changed_fraction * delta.size:
            self._pending, self._quiet = True, 0
            return False

        if not self._pending:
            return False
        self._quiet += 1
        if self._quiet < self._settle_frames:
            return False
        self._pending, self._quiet = False, 0
        return True

    def reset(self):
        """Forget the previous frame — call when capture pauses (target parked
        or switched) so a stale baseline can't fake or swallow a change."""
        self._prev = None
        self._pending = False
        self._quiet = 0
        self.sample = None
        self.mask = None

    def _sample(self, frame):
        # Every Nth pixel, BGR only. Kept as a wide int so the subtraction in
        # feed() can't wrap around.
        s = self._downscale
        return frame[::s, ::s, :3].astype(np.int16)
