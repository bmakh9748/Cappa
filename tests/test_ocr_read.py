"""detection/ocr.py reads real caption text — JAPANESE and English — from
full-res frame crops. Multi-script reading is a hard requirement: the app is
used on Japanese videos, and the text rules must see real text there (and
even if they don't, fail-open means captions still work — that path is
covered by test_classifier.py; THIS test proves the happy path).

Windowless, but loads the rec model (~2 s). Skips cleanly if no
Japanese-capable font is installed to render the sample."""

import sys
import time

import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from difflib import SequenceMatcher

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from cappa.detection.ocr import TextReader

FONTS = [r"C:\Windows\Fonts\YuGothM.ttc", r"C:\Windows\Fonts\meiryo.ttc",
         r"C:\Windows\Fonts\msgothic.ttc"]
font = None
for f in FONTS:
    try:
        font = ImageFont.truetype(f, 34)
        break
    except OSError:
        continue
if font is None:
    print("SKIP: no Japanese-capable font installed; cannot render samples")
    sys.exit(0)

SAMPLES = [
    ("japanese", "今日はいい天気ですね"),
    ("english", "The quick brown fox"),
]


def make_frame(text):
    """A 1080p-ish BGRA frame with one caption-style line at a known box."""
    rng = np.random.default_rng(2)
    img = Image.fromarray(
        rng.integers(50, 130, (720, 1280, 3), dtype=np.uint8))
    d = ImageDraw.Draw(img)
    d.text((340, 620), text, font=font, fill=(255, 255, 255),
           stroke_width=3, stroke_fill=(0, 0, 0))
    box = d.textbbox((340, 620), text, font=font, stroke_width=3)
    rgb = np.asarray(img)
    frame = np.empty((720, 1280, 4), np.uint8)
    frame[:, :, :3] = rgb[:, :, ::-1]  # RGB -> BGR
    frame[:, :, 3] = 255
    return frame, tuple(int(v) for v in box)


reader = TextReader()
t0 = time.perf_counter()
reader.warm()
assert reader.ready, "FAIL: rec model failed to load"
print("model load: %.1f s" % (time.perf_counter() - t0))

for name, want in SAMPLES:
    frame, box = make_frame(want)
    t0 = time.perf_counter()
    got, conf = reader.read(frame, box)
    ms = (time.perf_counter() - t0) * 1e3
    got = got or ""
    ratio = SequenceMatcher(None, want, got).ratio()
    assert ratio >= 0.6 and conf >= 0.6, (
        "FAIL: %s read %s (conf %.2f, match %.0f%%), wanted %s"
        % (name, ascii(got), conf, ratio * 100, ascii(want))
    )
    print("PASS: %s read %s (conf %.2f, match %.0f%%, %.0f ms)"
          % (name, ascii(got), conf, ratio * 100, ms))

# a sliver of a box: must not crash, must return no-evidence
got, conf = reader.read(frame, (0, 0, 1, 1))
assert got is None and conf == 0.0
print("PASS: degenerate crop returns no evidence, no crash")

print("ALL PASS")
