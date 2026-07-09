"""Text ALREADY on screen at lock-on (think: paused video) is judged and
boxed by the first scan, in every mode — the baseline/memorise pass is gone
along with the caption-vs-not gates (user call, 2026-07-09). Also drives the
manual refresh: after a forced refresh the same text is boxed again (the
'check again for words' path). Runs the real worker thread."""

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
    "background: black; color: white; font-size: 30px; font-weight: bold;"
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


def run_leg(settle=3.0, timeout=60.0, refresh=False):
    """Drive the real worker over the static window. Returns the 'appeared'
    boxes seen within `settle` seconds of the loop coming up.

    refresh=True: let the first scan land, discard what it boxed, then fire
    worker.refresh() and collect only what the forced re-scan boxes."""
    events, fps = [], []
    thread = QThread()
    worker = CaptureWorker(lambda: region)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.regions.connect(lambda p: events.extend(p[0]))
    worker.fps.connect(lambda v: fps.append(v))
    thread.start()
    t0 = time.perf_counter()
    ready_at = None
    fired = False
    while time.perf_counter() - t0 < timeout:
        app.processEvents()
        time.sleep(0.02)
        if not refresh and any(k == "appeared" for k, _ in events):
            break  # got what we came for (can beat the first fps report)
        if ready_at is None and fps:
            ready_at = time.perf_counter()
        if ready_at is not None and time.perf_counter() - ready_at >= settle:
            if refresh and not fired:
                # First scan has landed and boxed the caption. Drop that,
                # force a fresh judged scan, keep timing.
                events.clear()
                fired = True
                worker.refresh()
                ready_at = time.perf_counter()
                continue
            break
    worker.stop()
    thread.quit()
    thread.wait(3000)
    return [b for k, b in events if k == "appeared"]


# --- leg 1: the on-screen caption is boxed by the first scan ----------------
boxes = run_leg()
assert boxes, "FAIL: caption already on screen was not boxed at lock-on"
d = QApplication.primaryScreen().devicePixelRatio()
cx, cy = (90 + 460 / 2) * d, (260 + 60 / 2) * d  # label centre, physical px
hit = [b for b in boxes if b[0] < cx < b[2] and b[1] < cy < b[3]]
assert hit, (
    "FAIL: no box covers the caption centre (%r, %r): %r" % (cx, cy, boxes)
)
print("PASS: pre-existing caption is boxed by the first scan")

# --- leg 2: small text is judged too (no furniture memorising anymore) ------
caption.setStyleSheet(
    "background: black; color: white; font-size: 13px; font-weight: bold;"
)
caption.setText("small pre-existing line of text")
caption.setGeometry(140, 275, 360, 20)  # tight: the black label strip IS
app.processEvents()                     # the det box, so keep it small
app.processEvents()
boxes = run_leg()
assert boxes, "FAIL: small pre-existing text was not boxed at lock-on"
print("PASS: small pre-existing text is boxed too")

# --- leg 3: refresh re-scans and boxes the same text again ------------------
boxes = run_leg(refresh=True)
assert boxes, "FAIL: refresh did not re-scan the on-screen text"
print("PASS: refresh forces a fresh scan of on-screen text")

print("ALL PASS")
