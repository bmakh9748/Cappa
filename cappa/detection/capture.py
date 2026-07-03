"""Step 1 — screen capture.

Wraps `mss`, which does a raw pixel copy with no encoding: the plan's
"~5 ms, essentially free" capture step. An `mss` instance is bound to the
thread that created it, so each worker thread builds its own ScreenCapture
rather than sharing one."""

import mss
import numpy as np


class ScreenCapture:
    def __init__(self):
        self._sct = mss.mss()

    def grab(self, region):
        """Grab a screen rectangle.

        region: (left, top, width, height) in physical screen pixels.
        Returns an (H, W, 4) uint8 array in BGRA order — a private copy, since
        mss reuses its internal buffer on the next grab."""
        left, top, width, height = region
        shot = self._sct.grab(
            {"left": left, "top": top, "width": width, "height": height}
        )
        return np.array(shot, copy=True)

    def close(self):
        self._sct.close()
