"""Live end-to-end: real window, real capture thread. Background repaints a
different colour every iteration (a 'playing video' at real speed); a real
rendered text caption appears â€” the overlay must draw a box on it, and drop
the box when it disappears."""

import ctypes
import sys
import time

import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QWidget, QLabel
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor

from cappa.ui.overlay_window import OverlayWindow

COLORS = [QColor(192, 32, 32), QColor(32, 32, 192),
          QColor(32, 192, 32), QColor(192, 192, 32)]


class FakeVideo(QWidget):
    """Fills with a new colour each phase â€” cheap repaint, no re-polish."""

    def __init__(self):
        super().__init__()
        self.phase = 0
        self.setAttribute(Qt.WA_OpaquePaintEvent)

    def paintEvent(self, event):
        QPainter(self).fillRect(self.rect(), COLORS[self.phase % len(COLORS)])


app = QApplication(sys.argv)
overlay = OverlayWindow()
overlay.show()
overlay._tick.stop()

pip = FakeVideo()
pip.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
pip.setAttribute(Qt.WA_ShowWithoutActivating)
pip.setGeometry(200, 200, 640, 360)
pref = ctypes.c_int(1)  # square corners: exact bounds
pip.show()
ctypes.windll.dwmapi.DwmSetWindowAttribute(int(pip.winId()), 33, ctypes.byref(pref), 4)

caption = QLabel("SUBTITLE TEST LINE", pip)
caption.setStyleSheet(
    "background: black; color: white; font-size: 26px; font-weight: bold;"
)
caption.setAlignment(Qt.AlignCenter)
caption.setGeometry(120, 260, 400, 60)
caption.hide()
app.processEvents()

overlay._lock_to(int(pip.winId()))


def pump(seconds):
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        pip.phase += 1
        pip.update()
        if overlay._target_hwnd is not None:
            overlay._follow_target()
        app.processEvents()
        time.sleep(0.02)


# let the neural model finish loading before judging anything
while overlay._detector_ok is None:
    pump(0.3)

# 1. video playing, no caption: no boxes
pump(2.0)
assert overlay._captions == [], (
    "FAIL: boxes on plain video: %r" % overlay._captions
)
print("PASS: playing video alone yields no caption boxes")

# 2. caption appears: one box lands on the text
caption.show()
pump(2.5)
d = overlay._dpr()
assert len(overlay._captions) == 1, (
    "FAIL: expected 1 box, got %r" % overlay._captions
)
sentence = overlay._captions[0]
box, words = sentence.box, sentence.words
l, t, r, b = box
lab = (120 * d, 260 * d, 520 * d, 320 * d)   # label geometry, physical px
cx, cy = (lab[0] + lab[2]) / 2, (lab[1] + lab[3]) / 2
assert (l >= lab[0] - 16 and t >= lab[1] - 16
        and r <= lab[2] + 16 and b <= lab[3] + 16), (
    "FAIL: box %r outside label %r" % (box, lab)
)
assert l < cx < r and t < cy < b, (
    "FAIL: box %r misses label centre (%r, %r)" % (box, cx, cy)
)
assert "1 caption" in overlay.launcher.status_text(), \
    overlay.launcher.status_text()
print("PASS: live caption text boxed at %r (label %r)"
      % (box, tuple(int(v) for v in lab)))

# 2b. word hotspots: each Word sits inside the line box and knows its
# Sentence; hotspots are interactive; a click opens a popup carrying the
# Word, and the close button closes it
assert len(words) >= 2, "FAIL: expected word boxes, got %r" % (words,)
for w in words:
    wl, wt, wr, wb = w.box
    assert l - 8 <= wl <= wr <= r + 8 and t - 8 <= wt <= wb <= b + 8, (
        "FAIL: word box %r outside line %r" % (w.box, box)
    )
    assert w.sentence is sentence, "FAIL: word lost its sentence"
assert "".join(w.text for w in words).replace(" ", "") \
    == sentence.text.replace(" ", ""), "FAIL: words don't rebuild the line"
hotspots = overlay._word_rects()
assert len(hotspots) == len(words), "FAIL: hotspot/word count mismatch"
assert len(overlay._interactive_rects()) >= len(words), (
    "FAIL: word hotspots not interactive"
)
rect, word = hotspots[0]
overlay._popup.show_for(word, rect)
assert overlay._popup.isVisible(), "FAIL: popup did not open"
assert overlay._popup.word is word, "FAIL: popup lost the Word instance"
assert overlay._popup.geometry() in overlay._interactive_rects(), (
    "FAIL: open popup is not clickable"
)
from PySide6.QtWidgets import QPushButton

overlay._popup.findChild(QPushButton).click()
assert not overlay._popup.isVisible(), "FAIL: close button did not close it"
print("PASS: %d word hotspots; click opens popup carrying the Word, close "
      "button closes it (first: %r)" % (len(words), words[0].text))

# 3. caption disappears: box cleared promptly
caption.hide()
pump(1.5)
assert overlay._captions == [], (
    "FAIL: stale boxes after caption cleared: %r" % overlay._captions
)
assert "0 captions" in overlay.launcher.status_text()
print("PASS: box cleared when the caption vanished")

overlay._stop_capture()
print("ALL PASS")
