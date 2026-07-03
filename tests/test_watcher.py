"""Unit test for detection/stability.py â€” clear-watching over synthetic
frames: churning noise video with a striped caption band."""

import sys

import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from cappa.detection.diff import FrameDiff, DOWNSCALE
from cappa.detection.stability import CaptionWatcher, GRACE_FRAMES

H, W = 272, 480
BOX = (100, 200, 380, 228)
rng = np.random.default_rng(11)

diff = FrameDiff()
watcher = CaptionWatcher(scale=DOWNSCALE)
noise = rng.integers(0, 256, (136, W, 3), np.uint8)


def frame(caption=True):
    global noise
    noise = rng.integers(0, 256, (136, W, 3), np.uint8)
    f = np.zeros((H, W, 4), np.uint8)
    f[..., :3] = 40
    f[:136, :, :3] = noise
    if caption:
        l, t, r, b = BOX
        stripes = (np.arange(l, r) % 6 < 3) * np.uint8(255)
        f[t:b, l:r, :3] = stripes[None, :, None]
    return f


def run(n, caption=True):
    out = []
    for _ in range(n):
        diff.feed(frame(caption))
        if diff.mask is not None:
            out += watcher.feed(diff.sample, diff.mask)
    return out


# caption on screen for a while (quiet builds up), then start watching it
run(12)
watcher.watch(BOX)

# while the caption stays, video churn must never clear it
assert run(15) == [], "FAIL: cleared while the caption is still on screen"
print("PASS: live caption survives video churn")

# caption vanishes: cleared within a few frames
cleared = run(5, caption=False)
assert cleared == [BOX], "FAIL: expected [BOX], got %r" % cleared
print("PASS: vanished caption cleared promptly, exact box returned")

# fullscreen 1:1 video: compression shimmer wobbles caption pixels EVERY
# frame. Lost strokes are judged by current value, so shimmer self-recovers
# and the caption must survive indefinitely; a real vanish still clears.
def run_shimmer(n):
    out = []
    for _ in range(n):
        f = frame(True)
        l, t, r, b = BOX
        area = f[t:b, l:r, :3].astype(np.int16)
        mask = rng.random((b - t, r - l, 1)) < 0.15
        noise = rng.integers(-50, 51, area.shape, np.int16)
        f[t:b, l:r, :3] = np.clip(area + noise * mask, 0, 255).astype(np.uint8)
        diff.feed(f)
        if diff.mask is not None:
            out += watcher.feed(diff.sample, diff.mask)
    return out


run(12)
watcher.watch(BOX)
assert run_shimmer(30) == [], "FAIL: shimmer cleared a live caption"
cleared = run(6, caption=False)
assert cleared == [BOX], "FAIL: no clear after shimmer leg, got %r" % cleared
print("PASS: heavy shimmer never clears a caption; a real vanish still does")

# grace period: a box watched a beat before its caption vanishes still gets
# cleared â€” just not before GRACE_FRAMES have passed
run(12)
watcher.watch(BOX)
frames_needed = 0
for i in range(GRACE_FRAMES + 6):
    if run(1, caption=False):
        frames_needed = i + 1
        break
assert frames_needed > 0, "FAIL: never cleared after grace"
assert frames_needed >= GRACE_FRAMES - 1, (
    "FAIL: cleared during grace (frame %d)" % frames_needed
)
print("PASS: grace period holds, then the clear lands (frame %d)"
      % frames_needed)

# region resize: watcher resets itself instead of crashing or misfiring
run(12)
watcher.watch(BOX)
small = np.zeros((100, 100, 4), np.uint8)
diff.feed(small)
diff.feed(small)
assert watcher.feed(diff.sample, diff.mask) == []
print("PASS: resize resets cleanly")

print("ALL PASS")
