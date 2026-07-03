"""Unit test for pipeline/classifier.py â€” caption vs not-caption geometry."""

import sys
import time

import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cappa.detection.classifier import CaptionClassifier, text_verdict

SHAPE = (540, 960)  # (h, w) of a 960x540 tracked region


def accepts(c, box, shape=SHAPE):
    return c.filter([box], shape) == [box]


c = CaptionClassifier()

# centred, wide, caption-sized: the classic caption -> accepted
caption = (280, 440, 680, 480)  # 400x40, centred (cx=480)
assert accepts(c, caption)
print("PASS: centred caption-shaped band accepted")

# tiny text (timestamps, channel names) -> rejected
assert not accepts(c, (280, 440, 680, 449))  # 9px tall
print("PASS: too-small text rejected")

# huge band (title card / scene) -> rejected in a roomy region
assert not accepts(c, (200, 200, 760, 320))  # 120px tall > 14% of 540
print("PASS: too-tall band rejected")

# tall narrow block (poster, column of UI) -> rejected
assert not accepts(c, (430, 200, 530, 300))  # aspect 1.0
print("PASS: non-line aspect rejected")

# left-anchored text (YouTube hover title) with no history -> rejected
title = (20, 30, 420, 70)  # cx=220, far off centre
assert not accepts(c, title)
print("PASS: off-centre title rejected cold")

# ...and stays rejected even after a real caption was accepted: there is no
# "matches history" backdoor (it used to let chat lines of caption-ish
# height in once a real caption seeded it)
assert accepts(c, caption)
offcentre_same_zone = (60, 442, 380, 478)  # cx=220 (27% off), caption zone
assert not accepts(c, offcentre_same_zone), (
    "FAIL: off-centre box admitted via history backdoor"
)
print("PASS: no history backdoor for off-centre boxes in the caption zone")

# a burst (page scroll / redraw) -> whole batch rejected + cooldown
burst = [(100, 100 + i * 60, 500, 130 + i * 60) for i in range(5)]
assert c.filter(burst, SHAPE) == []
assert not accepts(c, caption), "cooldown should distrust stragglers"
time.sleep(0.75)
assert accepts(c, caption), "cooldown should have expired"
print("PASS: burst rejected, cooldown holds then releases")

# THE STREAM CASE: a churning chat overlay (many off-centre boxes) arriving
# in the same scan as the caption must never drag the caption down.
c_chat = CaptionClassifier()
chat = [(700, 80 + i * 60, 940, 110 + i * 60) for i in range(5)]  # right edge
assert c_chat.filter(chat + [caption], SHAPE) == [caption], (
    "FAIL: chat storm swallowed the caption"
)
assert all("off-centre" in why for _, why in c_chat.last_rejects)
print("PASS: chat storm rejected per-box, caption survives the same scan")

# cropped-strip mode: region barely taller than the caption itself ->
# the relative height cap must not apply
c2 = CaptionClassifier()
assert accepts(c2, (10, 4, 600, 44), shape=(52, 620))
print("PASS: cropped subtitle strip exempt from the height cap")

# text rules: FAIL-OPEN — real captions in ANY script pass, and so does
# anything the recogniser couldn't read confidently; only positively
# identified junk is rejected.
KEEP = [
    ("The quick brown fox", 0.97),      # English caption
    ("今日はいい天気ですね", 0.98),        # Japanese caption (isalpha covers CJK)
    ("これはOCRのテストです", 0.95),       # mixed script
    ("3人もいたの?", 0.9),               # digits inside a caption are fine
    ("gibberish#$@", 0.4),              # low confidence: no evidence
    (None, 0.0),                        # reader unavailable: no evidence
    ("", 0.0),                          # nothing read: no evidence
]
JUNK = [
    ("12:34", 0.98),                    # clock
    ("1080p60", 0.97),                  # player stats
    ("0 / 235", 0.95),                  # counter
    ("www.example.com", 0.96),          # watermark URL
    ("@some_channel", 0.96),            # handle
]
for text, conf in KEEP:
    assert text_verdict(text, conf) is None, (
        "FAIL: rejected a keeper: %r" % (text,)
    )
for text, conf in JUNK:
    assert text_verdict(text, conf) is not None, (
        "FAIL: junk passed: %r" % (text,)
    )
print("PASS: text rules keep captions (any script), reject confirmed junk")

print("ALL PASS")
