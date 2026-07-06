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

# accept the caption; the watermark is judged (rejected) and remembered
led.accept(CAPTION)
led.mark_seen([CAPTION, WATERMARK])
assert led.live() == [CAPTION]

# next scan: same caption (wobbled box) and same watermark -> nothing fresh
assert led.fresh([CAPTION_WOBBLE, WATERMARK]) == []
led.mark_seen([CAPTION_WOBBLE, WATERMARK])
print("PASS: live captions and remembered junk are not re-judged")

# the caption clears; the NEXT LINE in the same spot must be fresh
assert led.clear(CAPTION) is True
assert led.clear(CAPTION) is False, "double clear should be a no-op"
assert led.fresh([NEXT_LINE, WATERMARK]) == [NEXT_LINE], (
    "FAIL: next caption line in the same spot was suppressed"
)
print("PASS: cleared spot is immediately fresh for the next line")

# junk memory expires once the text has been gone for SEEN_TTL
tracking.SEEN_TTL = 0.1  # shrink for the test
led.mark_seen([WATERMARK])
time.sleep(0.15)
led.mark_seen([])  # a scan with the watermark gone -> entry expires
assert led.fresh([WATERMARK]) == [WATERMARK]
print("PASS: vanished junk is forgotten after the TTL")

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

# fingerprints: the same SPOT showing NEW content must be fresh again â€”
# this is the caption-line-change case (a memorised line must not mute the
# next one), while unchanged content stays suppressed.
import numpy as np

SCALE = 8


def sample(fill):
    s = np.zeros((70, 120, 3), np.int16)
    s[55:60, 35:85] = fill  # the caption zone on the downscaled grid
    return s


led2 = CaptionLedger()
led2.mark_seen([CAPTION], sample(200), SCALE)  # line 1 memorised (baseline)
assert led2.fresh([CAPTION], sample(200), SCALE) == [], (
    "unchanged content should stay suppressed"
)
changed = sample(200)
changed[55:60, 35:60] = 40  # the text changed
assert led2.fresh([CAPTION], changed, SCALE) == [CAPTION], (
    "FAIL: new content in a memorised spot was muted"
)
print("PASS: content change frees a memorised spot; same content stays muted")

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
assert led4.drifted(changed, SCALE) == [CAPTION], (
    "persistent content change must retire the live line"
)
assert led4.live() == []
assert led4.fresh([CAPTION], changed, SCALE) == [CAPTION], (
    "the replaced spot must be immediately fresh"
)
print("PASS: in-place content change retires the line; blips do not")

led.reset()
assert led.live() == [] and led.fresh([CAPTION]) == [CAPTION]
print("PASS: reset clears everything")

print("ALL PASS")
