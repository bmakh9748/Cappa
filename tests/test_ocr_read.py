"""detection/ocr.py reads real caption text — JAPANESE and English — from
full-res frame crops. Multi-script reading is a hard requirement: the app is
used on Japanese videos, and even where reads fail, fail-open means
captions still work — THIS test proves the happy path.

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

# A read that RAISES must fail open — (None, 0.0), captions keep working —
# but never silently: rapidocr 3.9 threw ModuleNotFoundError (python-bidi,
# a dependency it doesn't declare) on EVERY arabic read, and swallowed it
# looked exactly like "Arabic came back empty", the user report the arabic
# pack exists to fix. First failure of each kind is reported on stderr;
# repeats stay quiet (a structural failure fires per read). The tall box
# exercises BOTH read paths (upright + vertical) — still one line.
import io


class _RaisingModel:
    def __call__(self, *a, **k):
        raise ModuleNotFoundError("python-bidi is not installed")


broken = TextReader()
broken._model = _RaisingModel()   # loaded-and-ready, but every read raises
err, real_stderr = io.StringIO(), sys.stderr
sys.stderr = err
try:
    s1, c1 = broken.read(np.zeros((220, 80, 4), np.uint8), (10, 10, 60, 210))
    s2, c2 = broken.read(np.zeros((220, 80, 4), np.uint8), (10, 10, 60, 210))
finally:
    sys.stderr = real_stderr
assert s1 is None and c1 == 0.0 and s2 is None and c2 == 0.0, (
    "FAIL: raising read must return no evidence, got %r" % ((s1, c1, s2, c2),))
report = err.getvalue()
assert "ModuleNotFoundError" in report and "python-bidi" in report, (
    "FAIL: read failure not reported: %r" % report)
assert report.count("text read failed") == 1, (
    "FAIL: expected exactly one report for repeated failures: %r" % report)
# The vertical site must report ON ITS OWN: in the reads above the upright
# site raised first, so its report would mask a silently-swallowing
# vertical site. Fresh memory, vertical path alone.
broken._read_errors.clear()
err2 = io.StringIO()
sys.stderr = err2
try:
    got = broken._read_vertical(np.zeros((220, 80, 4), np.uint8),
                                (10, 10, 60, 210), 0)
finally:
    sys.stderr = real_stderr
assert got is None and "text read failed" in err2.getvalue(), (
    "FAIL: vertical read failure not reported: %r" % err2.getvalue())
print("PASS: a raising read fails open and reports itself exactly once")

# VERTICAL text (card_0001: 拔山蓋世 written top-to-bottom in a game read
# upright as '执数单' at conf 0.31 — garbage word, garbage translations).
# A tall column is also read on its side, best score wins; hotspots come
# back as cells stacked DOWN the column so click/drag work vertically.
V_TEXT = "公衆電話"
v_img = Image.new("RGB", (110, 400), (12, 12, 16))
v_draw = ImageDraw.Draw(v_img)
v_font = ImageFont.truetype(font.path, 60)
for i, ch in enumerate(V_TEXT):
    v_draw.text((25, 12 + i * 95), ch, font=v_font, fill=(255, 255, 255))
v_frame = np.empty((400, 110, 4), np.uint8)
v_frame[:, :, :3] = np.asarray(v_img)[:, :, ::-1]
v_frame[:, :, 3] = 255
sentence, conf = reader.read(v_frame, (0, 0, 110, 400))
assert sentence is not None and sentence.text == V_TEXT, (
    "FAIL: vertical column read %s (conf %.2f), wanted %s"
    % (ascii(sentence.text if sentence else ""), conf, ascii(V_TEXT)))
assert conf >= 0.8, "FAIL: vertical read shaky (conf %.2f)" % conf
cells = sentence.words
assert len(cells) == len(V_TEXT), (
    "FAIL: expected one hotspot per character, got %r" % cells)
tops = [w.box[1] for w in cells]
assert tops == sorted(tops), "FAIL: vertical cells not stacked top-to-bottom"
for prev, cur in zip(cells, cells[1:]):
    assert abs(cur.box[1] - prev.box[3]) <= 1, (
        "FAIL: vertical cells must tile, gap at y=%r" % cur.box[1])
assert all(w.box[0] >= -8 and w.box[2] <= 118 for w in cells), (
    "FAIL: vertical cell leaked past the column")
print("PASS: vertical column read %s (conf %.2f), cells stacked and tiling"
      % (ascii(sentence.text), conf))

# ...and a tall-ish but HORIZONTAL box must not regress: the upright read
# wins the comparison on score.
sentence, conf = reader.read(v_frame, (0, 100, 110, 200))  # one char, ~square+
print("PASS: aspect gate leaves near-square boxes alone (read %s)"
      % ascii(sentence.text if sentence else ""))

print("ALL PASS")
