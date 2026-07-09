"""Unit test for detection/classifier.py — the junk-text tag.

Detection accepts every text line (the caption-vs-not geometry gates were
deleted 2026-07-09); the only judgement left is whether a row's TEXT is junk
(clock/URL/handle), which keeps it off cards and out of the transcript while
staying clickable."""

import sys

import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cappa.detection.classifier import text_verdict

# FAIL-OPEN — real captions in ANY script pass, and so does anything the
# recogniser couldn't read confidently; only positively identified junk is
# stamped.
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
        "FAIL: stamped a keeper: %r" % (text,)
    )
for text, conf in JUNK:
    assert text_verdict(text, conf) is not None, (
        "FAIL: junk passed: %r" % (text,)
    )
print("PASS: text rules keep captions (any script), stamp confirmed junk")

print("ALL PASS")
