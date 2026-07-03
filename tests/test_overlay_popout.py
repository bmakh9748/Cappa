"""End-to-end check: PiP-popout tracking, deselect-on-close, and the live
capture -> diff chain with a real window changing on screen."""

import ctypes
import sys
import time

import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import Qt

from cappa import winapi
from cappa.ui.overlay_window import OverlayWindow

QApplication.setHighDpiScaleFactorRoundingPolicy(
    Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
)
app = QApplication(sys.argv)

overlay = OverlayWindow()
overlay.show()
overlay._tick.stop()  # drive the follow loop by hand, no key polling


def make_pip(color):
    """Always-on-top frameless window shown WITHOUT activation â€” the exact
    focus state of a browser's popped-out video. Corners squared, else Win11's
    rounded corners let the desktop (blinking terminal cursor!) bleed into the
    captured region and add noise triggers."""
    w = QWidget()
    w.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
    w.setAttribute(Qt.WA_ShowWithoutActivating)
    w.setStyleSheet(f"background: {color};")
    w.setGeometry(300, 300, 480, 270)
    w.show()
    pref = ctypes.c_int(1)  # DWMWCP_DONOTROUND
    ctypes.windll.dwmapi.DwmSetWindowAttribute(
        int(w.winId()), 33, ctypes.byref(pref), 4  # DWMWA_WINDOW_CORNER_PREFERENCE
    )
    app.processEvents()
    return w


def pump(seconds):
    """Keep the UI thread's event loop turning so queued signals from the
    capture thread arrive, while ticking the follow loop."""
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        if overlay._target_hwnd is not None:
            overlay._follow_target()
        app.processEvents()
        time.sleep(0.02)


# --- leg 1: tracking an unfocused topmost popout keeps the overlay up -------
pip = make_pip("#c02020")
pip_hwnd = int(pip.winId())
overlay_hwnd = int(overlay.winId())
assert winapi.is_topmost(pip_hwnd)
assert winapi.foreground_root() not in (pip_hwnd, overlay_hwnd)

overlay._lock_to(pip_hwnd)
pump(0.3)
assert not overlay._parked, "FAIL: parked while tracking a topmost popout"
assert winapi.is_above(overlay_hwnd, pip_hwnd), "FAIL: overlay under the popout"
print("PASS: overlay visible and above the unfocused popout")

# --- leg 2: live worker against the popout: fps reported, and a flat colour
# flip never becomes a "caption" ---------------------------------------------
deadline = time.perf_counter() + 90  # model import/load, then first fps
while "fps" not in overlay.launcher.status_text():
    assert time.perf_counter() < deadline, (
        "FAIL: no fps after 20s: %r" % overlay.launcher.status_text()
    )
    pump(0.5)
pip.setStyleSheet("background: #2020c0;")  # red -> blue: one visual change
pip.update()
pump(2.0)
assert overlay._text_boxes == [], (
    "FAIL: a flat colour flip must not become a caption: %r"
    % overlay._text_boxes
)
assert "0 captions" in overlay.launcher.status_text(), \
    overlay.launcher.status_text()
print("PASS: live worker runs, flat colour flip is not a caption; status: %r"
      % overlay.launcher.status_text())

# --- leg 3: pressing X (apps that HIDE the panel) => deselect, not vanish ---
pip.hide()
app.processEvents()
overlay._follow_target()
assert overlay._target_hwnd is None, "FAIL: still tracking a hidden popout"
assert not overlay._parked, "FAIL: overlay parked instead of deselecting"
assert overlay.launcher.isVisible(), "FAIL: launcher gone after popout hidden"
assert "closed" in overlay.launcher.status_text().lower()
assert not overlay.isVisible(), "FAIL: idle overlay should be hidden"
print("PASS: hiding the popout deselects back to idle")

# --- leg 4: pressing X (apps that DESTROY the panel) => same deselect -------
pip2 = make_pip("#20c020")
overlay._lock_to(int(pip2.winId()))
pump(0.3)
assert not overlay._parked
pip2.destroy()
app.processEvents()
overlay._follow_target()
assert overlay._target_hwnd is None, "FAIL: still tracking a dead window"
assert overlay.launcher.isVisible()
assert "closed" in overlay.launcher.status_text().lower()
print("PASS: destroying the popout deselects back to idle")

while overlay._detector_ok is None:  # let the model load finish so the
    pump(0.3)                        # worker thread can stop cleanly
overlay._stop_capture()
print("ALL PASS")
