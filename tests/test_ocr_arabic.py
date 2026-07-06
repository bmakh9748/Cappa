"""Unit test: the video-language -> rec-model mapping actually reads Arabic.

The default multi-script model cannot read Arabic script at all (it returns
an empty string -- the exact user report), so picking Arabic in Settings must
load the arabic rec pack. Renders a caption with Qt (which shapes Arabic
correctly -- PIL does not) and reads it through the real TextReader, both at
construction and via a live set_language() swap. Loads neural models: a few
seconds on first run while the pack downloads (~8 MB), instant after.

Windowless (QGuiApplication only; nothing is shown)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Piped/redirected Windows consoles default to cp1252, which can't print the
# Arabic this test reads back — same guard the app itself uses.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter

from cappa.detection.ocr import TextReader

ARABIC = "مرحبا بالعالم"   # "hello world"


def render_caption(text):
    """(H, W, 4) BGRA uint8 frame with `text` white-on-black, like a subtitle.
    Format_ARGB32 on little-endian x86 is BGRA in memory -- the same layout
    the capture pipeline hands the reader."""
    img = QImage(640, 80, QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0))
    p = QPainter(img)
    p.setPen(QColor(255, 255, 255))
    font = QFont("Arial", 28)
    font.setBold(True)
    p.setFont(font)
    p.drawText(img.rect(), Qt.AlignCenter, text)
    p.end()
    arr = np.frombuffer(img.constBits(), dtype=np.uint8).reshape(80, 640, 4)
    return arr.copy()


def is_arabic(s):
    return any("؀" <= ch <= "ۿ" for ch in s or "")


app = QGuiApplication(sys.argv)
frame = render_caption(ARABIC)
box = (60, 10, 580, 70)

reader = TextReader(lang="ar")
reader.warm()
assert reader.ready, "arabic reader failed to load"
sentence, conf = reader.read(frame, box)
assert sentence is not None and is_arabic(sentence.text), (
    "arabic pack did not read Arabic: %r" % (sentence and sentence.text))
print("PASS arabic-at-construction: %r (%.2f)"
      % (sentence.text, conf))

# The card-15 regression: hotspot words came out mirrored (visual CTC order)
# while the line text was logical, so a clicked word matched nothing and
# translated to garbage. Every hotspot's text must appear in the sentence.
assert len(sentence.words) >= 2, (
    "expected word-level hotspots, got %d" % len(sentence.words))
for w in sentence.words:
    assert w.text in sentence.text, (
        "hotspot %r is not a substring of sentence %r -- reversed?"
        % (w.text, sentence.text))
print("PASS logical-order hotspots: %s"
      % " | ".join(repr(w.text) for w in sentence.words))

# The same swap Settings performs live: default reader -> Arabic.
reader2 = TextReader()
reader2.warm()
s_default, _ = reader2.read(frame, box)
assert not is_arabic(s_default.text if s_default else ""), (
    "default model unexpectedly reads Arabic now -- mapping may be obsolete")
reader2.set_language("ar")
s_after, conf_after = reader2.read(frame, box)
assert s_after is not None and is_arabic(s_after.text), (
    "set_language('ar') did not switch the model: %r"
    % (s_after and s_after.text))
print("PASS live-switch: default read %r -> arabic read %r (%.2f)"
      % (s_default.text if s_default else None, s_after.text, conf_after))

# Unknown/Latin languages keep the default model object behavior (no crash,
# still reads English).
reader2.set_language("es")
s_en, _ = reader2.read(render_caption("hello world"), box)
assert s_en is not None and "hello" in (s_en.text or "").lower(), (
    "default model regressed after switching back: %r" % (s_en and s_en.text))
print("PASS back-to-default: read %r" % s_en.text)
print("ALL PASS")
