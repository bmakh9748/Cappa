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
assert overlay._text_boxes == [], (
    "FAIL: boxes on plain video: %r" % overlay._text_boxes
)
print("PASS: playing video alone yields no caption boxes")

# 2. caption appears: one box lands on the text
caption.show()
pump(2.5)
d = overlay._dpr()
assert len(overlay._text_boxes) == 1, (
    "FAIL: expected 1 box, got %r" % overlay._text_boxes
)
l, t, r, b = overlay._text_boxes[0]
lab = (120 * d, 260 * d, 520 * d, 320 * d)   # label geometry, physical px
cx, cy = (lab[0] + lab[2]) / 2, (lab[1] + lab[3]) / 2
assert (l >= lab[0] - 16 and t >= lab[1] - 16
        and r <= lab[2] + 16 and b <= lab[3] + 16), (
    "FAIL: box %r outside label %r" % (overlay._text_boxes[0], lab)
)
assert l < cx < r and t < cy < b, (
    "FAIL: box %r misses label centre (%r, %r)" % (overlay._text_boxes[0], cx, cy)
)
assert "1 caption" in overlay.launcher.status_text(), \
    overlay.launcher.status_text()
print("PASS: live caption text boxed at %r (label %r)"
      % (overlay._text_boxes[0], tuple(int(v) for v in lab)))

# 3. caption disappears: box cleared promptly
caption.hide()
pump(1.5)
assert overlay._text_boxes == [], (
    "FAIL: stale boxes after caption cleared: %r" % overlay._text_boxes
)
assert "0 captions" in overlay.launcher.status_text()
print("PASS: box cleared when the caption vanished")

overlay._stop_capture()
print("ALL PASS")
