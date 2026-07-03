"""A user-drawn area must be searched immediately: a caption ALREADY on
screen when the area lands (think: paused video) is judged by the first scan
instead of being memorised as page furniture. Control leg: a whole-window
lock still memorises it (the search-bar rule), so nothing is boxed until the
next caption line."""

import ctypes
import sys
import time

import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QWidget, QLabel
from PySide6.QtCore import Qt, QThread

from cappa import winapi
from cappa.detection.worker import CaptureWorker

QApplication.setHighDpiScaleFactorRoundingPolicy(
    Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
)
app = QApplication(sys.argv)

# A static window — a paused video — with its caption already visible.
win = QWidget()
win.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
win.setAttribute(Qt.WA_ShowWithoutActivating)
win.setStyleSheet("background: #202028;")
win.setGeometry(200, 200, 640, 360)
caption = QLabel("A CAPTION ALREADY ON SCREEN", win)
caption.setStyleSheet(
    "background: black; color: white; font-size: 26px; font-weight: bold;"
)
caption.setAlignment(Qt.AlignCenter)
caption.setGeometry(90, 260, 460, 60)
win.show()
pref = ctypes.c_int(1)  # DWMWCP_DONOTROUND, keep bounds exact
ctypes.windll.dwmapi.DwmSetWindowAttribute(
    int(win.winId()), 33, ctypes.byref(pref), 4)
app.processEvents()

wl, wt, wr, wb = winapi.extended_frame_bounds(int(win.winId()))
region = (wl, wt, wr - wl, wb - wt)


def run_leg(user_area, settle=3.0, timeout=60.0):
    """Drive the real worker over the static window. Returns the 'appeared'
    boxes seen within `settle` seconds after the loop starts reporting fps
    (i.e. well after the baseline/first scan)."""
    events, fps = [], []
    thread = QThread()
    worker = CaptureWorker(
        lambda: region,
        user_area_provider=(lambda: True) if user_area else None,
    )
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.regions.connect(lambda p: events.extend(p[0]))
    worker.fps.connect(lambda v: fps.append(v))
    thread.start()
    t0 = time.perf_counter()
    ready_at = None
    while time.perf_counter() - t0 < timeout:
        app.processEvents()
        time.sleep(0.02)
        if user_area and any(k == "appeared" for k, _ in events):
            break  # got what we came for (can beat the first fps report)
        if ready_at is None and fps:
            ready_at = time.perf_counter()
        if ready_at is not None and time.perf_counter() - ready_at >= settle:
            break
    if not user_area:  # control leg must have watched the loop actually run
        assert ready_at is not None, "FAIL: worker never reported fps"
    worker.stop()
    thread.quit()
    thread.wait(3000)
    return [b for k, b in events if k == "appeared"]


# --- leg 1: user-drawn area => the on-screen caption is boxed immediately ---
boxes = run_leg(user_area=True)
assert boxes, ("FAIL: caption already on screen was not boxed after "
               "selecting the area")
d = QApplication.primaryScreen().devicePixelRatio()
cx, cy = (90 + 460 / 2) * d, (260 + 60 / 2) * d  # label centre, physical px
l, t, r, b = boxes[0]
assert l < cx < r and t < cy < b, (
    "FAIL: box %r misses the caption centre (%r, %r)" % (boxes[0], cx, cy)
)
print("PASS: user-drawn area judges pre-existing caption on the first scan")

# --- leg 2: whole-window lock => memorised, NOT boxed (search-bar rule) -----
boxes = run_leg(user_area=False)
assert not boxes, (
    "FAIL: whole-window lock must memorise pre-existing text, boxed %r"
    % boxes
)
print("PASS: whole-window lock still memorises pre-existing text")

print("ALL PASS")
