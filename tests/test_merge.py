"""Unit test for detector.merge_lines — DBNet returns big/spaced/italic
captions as several fragments; downstream wants one box per text line."""

import sys

import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cappa.detection.detector import merge_lines

# fragments of one big spaced line (real boxes from a user screenshot of a
# stylised caption) -> one box spanning the whole line
FRAGS = [(4, 158, 201, 237), (198, 170, 336, 229), (330, 166, 522, 236),
         (525, 163, 675, 235)]
assert merge_lines(FRAGS) == [(4, 158, 675, 237)]
print("PASS: same-line fragments merge into one line box")

# stacked lines (caption + shout below, no vertical overlap) -> separate
lines = [(100, 100, 500, 140), (150, 150, 450, 190)]
assert sorted(merge_lines(lines)) == sorted(lines)
print("PASS: stacked lines stay separate")

# same row but far apart (gap >> glyph height): separate UI elements
far = [(0, 100, 100, 130), (400, 100, 500, 130)]
assert sorted(merge_lines(far)) == sorted(far)
print("PASS: distant same-row text stays separate")

# input order must not matter
assert merge_lines(list(reversed(FRAGS))) == [(4, 158, 675, 237)]
print("PASS: merge is order-independent")

print("ALL PASS")
