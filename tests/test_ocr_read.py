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

from cappa import lexicon
from cappa.detection.ocr import TextReader, _lexicon_split, _respace, _respan

# _lexicon_split (pure): a glued run is broken ONLY into pieces that are
# every one a real word; the split decision comes from the word list, not
# from a lucky second reading. card_0027's 'YOU CANALWAYS' -> 'YOU CAN
# ALWAYS'. A tiny in-memory pack stands in for a downloaded one.
lexicon._packs["en"] = {w: i for i, w in enumerate(
    ["you", "can", "always", "family", "members", "the", "it"])}
try:
    assert _lexicon_split("YOU CANALWAYS", "en") == "YOU CAN ALWAYS"
    assert _lexicon_split("FAMILYMEMBERS", "en") == "FAMILY MEMBERS"
    # A real word is never torn apart, even though 'always' contains no
    # shorter known split here.
    assert _lexicon_split("ALWAYS", "en") == "ALWAYS"
    # An OCR letter error ('YOUCAL' — N misread as L) has no all-known
    # split and is left exactly as read.
    assert _lexicon_split("YOUCAL ALWAYS", "en") == "YOUCAL ALWAYS"
    # No pack for a language -> nothing changes (only ever ADDS splitting).
    assert _lexicon_split("CANALWAYS", "de") == "CANALWAYS"
    # Case is preserved; matching is case-insensitive.
    assert lexicon.split("CanAlways", "en") == ["Can", "Always"]
finally:
    lexicon._packs.pop("en", None)
print("PASS: the lexicon splits glued runs into real words, spares words "
      "and OCR errors")

# _respan: the base read's geometry re-cut to the merged words. 'YOUCANALWAYS'
# spans x=100..460 as two spans; after the merge a hotspot must exist for each
# of the three words, tiling the same pixels.
merged = _respan([("YOU", (100, 10, 190, 70)), ("CANALWAYS", (190, 10, 460, 70))],
                 "YOU CAN ALWAYS")
assert [w for w, _ in merged] == ["YOU", "CAN", "ALWAYS"], merged
assert merged[0][1] == (100, 10, 190, 70), merged
assert merged[1][1][0] == 190 and merged[2][1][2] == 460, merged
assert merged[1][1][2] == merged[2][1][0], "words must tile, not overlap"
# Characters that don't line up leave the spans untouched.
same = [("HELLO", (0, 0, 50, 10))]
assert _respan(same, "HEL LO THERE") == same
print("PASS: re-cut spans tile the line, one hotspot per merged word")

# _respace (pure, no model): a line whose text lost a space between words
# (card_0044: 'KARENA APA' read as 'KARENAAPA') is rebuilt from the word
# spans; already-correct text, CJK lines and mismatched spans are untouched.
B = (0, 0, 1, 1)  # span boxes are irrelevant here
assert _respace("KARENAAPA!?", [("KARENA", B), ("APA!?", B)]) == "KARENA APA!?"
assert _respace("KARENA APA!?", [("KARENA", B), ("APA!?", B)]) == "KARENA APA!?"
assert _respace("木漏れ日", [("木漏", B), ("れ日", B)]) == "木漏れ日"
assert _respace("mismatch", [("mis", B), ("take", B)]) == "mismatch"
assert _respace("oneword", [("oneword", B)]) == "oneword"
print("PASS: dropped spaces rebuilt from word spans (never CJK/mismatches)")

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
    sentence, conf = reader.read(frame, box)
    ms = (time.perf_counter() - t0) * 1e3
    got = sentence.text if sentence else ""
    ratio = SequenceMatcher(None, want, got).ratio()
    assert ratio >= 0.6 and conf >= 0.6, (
        "FAIL: %s read %s (conf %.2f, match %.0f%%), wanted %s"
        % (name, ascii(got), conf, ratio * 100, ascii(want))
    )
    # Word geometry: several units, each horizontally inside the line box,
    # tiling its text left to right with no gaps (that gap-free property
    # is what keeps a hover from landing 'between' words), and each Word
    # knowing its Sentence.
    words = sentence.words
    assert len(words) >= 2, (
        "FAIL: %s expected word boxes, got %r" % (name, words)
    )
    l, t, r, b = box
    prev_r = None
    for w in words:
        wl, wt, wr, wb = w.box
        assert w.text, "FAIL: empty word text"
        assert w.sentence is sentence, "FAIL: word lost its sentence"
        assert l - 12 <= wl < wr <= r + 12, (
            "FAIL: %s word %s box %r outside line %r"
            % (name, ascii(w.text), w.box, box)
        )
        assert prev_r is None or abs(wl - prev_r) <= 1, (
            "FAIL: %s gap between words at x=%r" % (name, wl)
        )
        prev_r = wr
    assert "".join(w.text for w in words).replace(" ", "") \
        == got.replace(" ", ""), "FAIL: word texts don't rebuild the line"
    print("PASS: %s read %s (conf %.2f, match %.0f%%, %d words, %.0f ms)"
          % (name, ascii(got), conf, ratio * 100, len(words), ms))

# a sliver of a box: must not crash, must return no-evidence
sentence, conf = reader.read(frame, (0, 0, 1, 1))
assert sentence is None and conf == 0.0
print("PASS: degenerate crop returns no evidence, no crash")

print("ALL PASS")
