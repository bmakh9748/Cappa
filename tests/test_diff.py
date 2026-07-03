"""Unit test for pipeline/diff.py with synthetic BGRA frames."""

import sys

import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from cappa.detection.diff import FrameDiff

H, W = 270, 480


def blank():
    f = np.zeros((H, W, 4), dtype=np.uint8)
    f[..., :3] = 30  # dark video background
    return f


def with_subtitle(brightness):
    f = blank()
    f[200:230, 100:380, :3] = brightness  # a subtitle-shaped bright band
    return f


d = FrameDiff(settle_frames=3)

# First frame arms the "pending" state (everything is new) â€¦
assert d.feed(blank()) is False
# â€¦ and three quiet frames later it settles: exactly one fire.
fires = [d.feed(blank()) for _ in range(6)]
assert fires == [False, False, True, False, False, False], fires
print("PASS: initial frame settles once, then static frames stay quiet")

# Subtitle fades in over 3 frames: no fire during the fade â€¦
assert d.feed(with_subtitle(90)) is False
assert d.feed(with_subtitle(170)) is False
assert d.feed(with_subtitle(255)) is False
# â€¦ then fires exactly once after 3 settled frames of the final subtitle.
fires = [d.feed(with_subtitle(255)) for _ in range(6)]
assert fires == [False, False, True, False, False, False], fires
print("PASS: fade-in fires once, on the settled subtitle")

# Subtitle disappears: that change also fires once after settling.
assert d.feed(blank()) is False
fires = [d.feed(blank()) for _ in range(4)]
assert fires == [False, False, True, False], fires
print("PASS: subtitle clearing fires once")

# A tiny change (below the fraction gate) never fires.
tiny = blank()
tiny[0:2, 0:2, :3] = 255  # 4 px out of ~130k
assert d.feed(tiny) is False
assert not any(d.feed(tiny) for _ in range(5))
print("PASS: sub-threshold noise ignored")

# Region resized: re-arms and settles once, no crash on shape mismatch.
small = np.zeros((100, 100, 4), dtype=np.uint8)
assert d.feed(small) is False
fires = [d.feed(small) for _ in range(4)]
assert fires == [False, False, True, False], fires
print("PASS: resize re-arms cleanly")

# reset(): stale baseline dropped; resuming re-arms instead of comparing
# across the gap.
d.feed(with_subtitle(255))
d.reset()
assert d.feed(with_subtitle(255)) is False  # first frame after resume
fires = [d.feed(with_subtitle(255)) for _ in range(4)]
assert fires == [False, False, True, False], fires
print("PASS: reset() drops the baseline")

print("ALL PASS")
