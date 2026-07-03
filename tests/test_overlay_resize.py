"""End-to-end check of region edge-resizing. The launcher lives in its own
window at the screen corner, so even the tiniest selection must leave it
usable, and the region's edge grips recover a too-small selection."""

import ctypes
import sys
import time

import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import Qt, QPoint

from cappa import winapi
from cappa.ui.overlay_window import OverlayWindow, MIN_SELECTION

app = QApplication(sys.argv)
overlay = OverlayWindow()
overlay.show()
overlay._tick.stop()

pip = QWidget()
pip.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
pip.setAttribute(Qt.WA_ShowWithoutActivating)
pip.setStyleSheet("background: #c02020;")
pip.setGeometry(200, 200, 800, 400)
pip.show()
pref = ctypes.c_int(1)  # DWMWCP_DONOTROUND, keep bounds exact
ctypes.windll.dwmapi.DwmSetWindowAttribute(int(pip.winId()), 33, ctypes.byref(pref), 4)
app.processEvents()


def settle(n=4):
    for _ in range(n):
        overlay._follow_target()
        app.processEvents()
        time.sleep(0.02)


overlay._lock_to(int(pip.winId()))
settle()

# --- a too-narrow selection must not cost the user the controls -------------
overlay._region = (0.30, 0.80, 0.50, 0.87)  # ~160x28 logical strip
settle()
assert overlay.height() < 40, "region should be a narrow strip, got %r" % overlay.geometry()
assert overlay.launcher.isVisible(), \
    "FAIL: launcher must stay up regardless of region size"
assert overlay._region_resizable(), "FAIL: strip should be edge-resizable"
rects = overlay._interactive_rects()
assert len(rects) == 4, "FAIL: expected 4 edge bands, got %d" % len(rects)
print("PASS: narrow selection keeps the launcher usable, edges interactive")

# --- edge hit-testing --------------------------------------------------------
w, h = overlay.width(), overlay.height()
assert overlay._edge_hit(QPoint(2, h // 2)) == "L"
assert overlay._edge_hit(QPoint(w - 2, 2)) == "TR"
assert overlay._edge_hit(QPoint(w // 2, h - 2)) == "B"
assert overlay._edge_hit(QPoint(w // 2, h // 2)) == ""  # middle: click-through
assert overlay._edge_hit(QPoint(-5, 5)) == ""           # outside the window
print("PASS: edge hit-testing (sides, corners, interior, outside)")

# --- capture pauses mid-drag -------------------------------------------------
overlay._resize_edges = "TL"
assert overlay.capture_region() is None, "FAIL: capture should pause mid-drag"

# --- drag the region out like a window ---------------------------------------
real_cursor_pos = winapi.cursor_pos
wl, wt, wr, wb = winapi.extended_frame_bounds(int(pip.winId()))

winapi.cursor_pos = lambda: (wl - 100, wt - 100)  # drag TL past the corner
overlay._drag_region_edges()
overlay._resize_edges = "BR"
winapi.cursor_pos = lambda: (wr + 100, wb + 100)  # drag BR past the corner
overlay._drag_region_edges()
overlay._resize_edges = ""
winapi.cursor_pos = real_cursor_pos
settle()

fl, ft, fr, fb = overlay._region
assert (fl, ft, fr, fb) == (0.0, 0.0, 1.0, 1.0), (
    "FAIL: region should clamp to the full window, got %r" % (overlay._region,)
)
assert overlay.launcher.isVisible()
assert overlay.capture_region() is not None
print("PASS: dragging the edges out restores the region")

# --- minimum size clamp -------------------------------------------------------
overlay._resize_edges = "L"
winapi.cursor_pos = lambda: (wr + 500, wt)  # try to push L past R
overlay._drag_region_edges()
overlay._resize_edges = ""
winapi.cursor_pos = real_cursor_pos
l, t, r, b = overlay._target_bounds()
min_phys = MIN_SELECTION * overlay._dpr()
assert abs((r - l) - min_phys) < 2, (
    "FAIL: width should clamp to %spx, got %s" % (min_phys, r - l)
)
print("PASS: region can't be collapsed below the minimum size")

while overlay._detector_ok is None:  # let the model load finish so the
    settle(5)                        # worker thread can stop cleanly
overlay._stop_capture()
print("ALL PASS")
