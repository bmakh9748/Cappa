"""Unit test for detection/tracking.py â€” the caption ledger."""

import sys
import time

import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cappa.detection import tracking
from cappa.detection.tracking import CaptionLedger

CAPTION = (280, 440, 680, 480)
CAPTION_WOBBLE = (283, 438, 676, 482)   # same text, next scan's box
NEXT_LINE = (300, 442, 650, 478)        # a different line, same spot
WATERMARK = (20, 20, 180, 50)

led = CaptionLedger()

# an empty ledger: everything in a scan is fresh
assert led.fresh([CAPTION, WATERMARK]) == [CAPTION, WATERMARK]

# accept the caption: a live box is not re-read on the next scan, even
# through a little box wobble; other text stays fresh
led.accept(CAPTION)
assert led.live() == [CAPTION]
assert led.fresh([CAPTION_WOBBLE, WATERMARK]) == [WATERMARK]
print("PASS: live captions are not re-read; new text is fresh")

# the caption clears; the NEXT LINE in the same spot must be fresh
assert led.clear(CAPTION) is True
assert led.clear(CAPTION) is False, "double clear should be a no-op"
assert led.fresh([NEXT_LINE]) == [NEXT_LINE], (
    "FAIL: next caption line in the same spot was suppressed"
)
print("PASS: cleared spot is immediately fresh for the next line")

# sweep: a live caption that scans stop seeing gets retired at MISS_LIMIT
led.reset()
led.accept(CAPTION)
assert led.sweep([CAPTION_WOBBLE]) == [], "seen wobbled = not a miss"
assert led.sweep([]) == [] and led.sweep([]) == []
assert led.sweep([]) == [CAPTION], "third consecutive miss retires it"
assert led.live() == []
led.accept(CAPTION)
led.sweep([])
led.sweep([CAPTION])  # reappearing resets the miss count
assert led.sweep([]) == [] and led.sweep([]) == []
print("PASS: sweep retires unseen captions, reappearing resets the count")

# fingerprints (used by resurrect/drift below): a coarse content signature
# of the pixels inside a box, on the diff's downscaled grid.
import numpy as np

SCALE = 8


def sample(fill):
    s = np.zeros((70, 120, 3), np.int16)
    s[55:60, 35:85] = fill  # the caption zone on the downscaled grid
    return s


changed = sample(200)
changed[55:60, 35:60] = 40  # the text changed

# clear hysteresis: a suspected clear stays PENDING; the same spot coming
# back with the accept-time content is resurrected silently (a gradient or
# popup blip over the caption), while a real clear expires and surfaces.
tracking.CLEAR_CONFIRM = 0.1  # shrink for the test
led3 = CaptionLedger()
led3.accept(CAPTION, sample(200), SCALE)
assert led3.clear(CAPTION) is True
assert led3.live() == [] and led3.expire_clears() == [], (
    "a pending clear must not expire instantly"
)
assert led3.resurrect([CAPTION_WOBBLE], sample(200), SCALE) == [CAPTION_WOBBLE]
assert led3.live() == [CAPTION_WOBBLE], "resurrected caption should be live"
time.sleep(0.15)
assert led3.expire_clears() == [], "a resurrected clear must never surface"
# real clear: the spot shows different content at the confirming scan
assert led3.clear(CAPTION_WOBBLE) is True
assert led3.resurrect([NEXT_LINE], changed, SCALE) == [], (
    "different content must not resurrect"
)
time.sleep(0.15)
assert led3.expire_clears() == [CAPTION_WOBBLE], "a real clear must surface"
print("PASS: blip clears resurrect silently; real clears surface on expiry")

# drift: a live caption whose CONTENT changes in place (the next line drawn
# over the same spot with no clean vanish between) is retired once the change
# persists past DRIFT_CONFIRM, and the spot is immediately fresh — the
# automatic version of the manual refresh this used to require. A momentary
# blip (control-bar gradient) that reverts must NOT retire it.
tracking.DRIFT_CONFIRM = 0.1
led4 = CaptionLedger()
led4.accept(CAPTION, sample(200), SCALE)
assert led4.drifted(sample(200), SCALE) == [], "unchanged content drifted?"
assert led4.drifted(changed, SCALE) == [], "drift must not retire instantly"
assert led4.drifted(sample(200), SCALE) == [], "reverted blip retired the line"
time.sleep(0.15)
assert led4.drifted(sample(200), SCALE) == [], (
    "unchanged content retired after the blip reset"
)
assert led4.live() == [CAPTION]
assert led4.drifted(changed, SCALE) == []      # drift first noticed
time.sleep(0.15)
retired = led4.drifted(changed, SCALE)
assert [b for b, _ in retired] == [CAPTION], (
    "persistent content change must retire the live line"
)
assert led4.live() == []
assert led4.fresh([CAPTION]) == [CAPTION], (
    "the replaced spot must be immediately fresh"
)
# drifted() hands back the sentence the box was showing, so the worker can
# re-read and tell a REAL replacement from shimmer under unchanged text
# (2026-07-18: the same caption was retired+re-read in a loop). Re-accepting
# that same sentence must keep its appeared_at — it anchors the audio clip.
box, sent = retired[0]
assert sent is not None and sent.appeared_at > 0, (
    "the retired sentence (with its appeared_at) must ride along"
)
born = sent.appeared_at
led4.accept(box, sample(200), SCALE, sent, born)
assert led4.live() == [CAPTION]
assert led4.captions()[0].appeared_at == born, (
    "re-accepting the shimmer-drifted sentence must not re-anchor its clip"
)
assert led4.drifted(sample(200), SCALE) == [], (
    "re-baselined fingerprint must match the current pixels again"
)
print("PASS: in-place content change retires the line; blips do not; "
      "shimmer re-accept keeps the clip anchor")

led.reset()
assert led.live() == [] and led.fresh([CAPTION]) == [CAPTION]
print("PASS: reset clears everything")

print("ALL PASS")
