"""Tracking a WHOLE browser window in default YouTube layout — video pane
left of centre, UI text everywhere (tab title, video title, churning chat).
Detection accepts every text line (the caption-vs-not gates are gone, user
call 2026-07-09: page text becomes hoverable words too — that's the deal);
what must hold is the caption's own life amid the churn: boxed at lock-on,
re-read when the line changes, cleared when it leaves. Runs the real
production worker."""

import ctypes
import sys
import time

import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QWidget, QLabel
from PySide6.QtCore import Qt, QThread
from PySide6.QtGui import QPainter, QColor, QPen

from cappa import winapi
from cappa.detection.worker import CaptureWorker

COLORS = [QColor(160, 60, 40), QColor(40, 60, 160),
          QColor(40, 140, 60), QColor(150, 140, 50)]


class BoxLayer(QWidget):
    """Transparent top layer showing what detection accepted — outline only,
    drawn OUTSIDE the box, so the ink doesn't disturb the pixels the watcher
    guards (this window is captured, unlike the real overlay)."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.boxes = []

    def paintEvent(self, event):
        if not self.boxes:
            return
        p = QPainter(self)
        p.setPen(QPen(QColor(90, 210, 255), 2))
        for l, t, r, b in self.boxes:
            p.drawRect(int(l) - 4, int(t) - 4, int(r - l) + 8, int(b - t) + 8)


class Video(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.phase = 0
        self.setAttribute(Qt.WA_OpaquePaintEvent)

    def paintEvent(self, event):
        QPainter(self).fillRect(self.rect(), COLORS[self.phase % len(COLORS)])


app = QApplication(sys.argv)

browser = QWidget()
browser.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
browser.setAttribute(Qt.WA_ShowWithoutActivating)
browser.setStyleSheet("background: #181818; color: #e8e8e8;")
browser.setGeometry(2, 2, 1272, 692)


def ui_text(text, x, y, w, h, size=13, bold=False):
    lab = QLabel(text, browser)
    lab.setStyleSheet("font-size: %dpx; %s background: transparent;"
                      % (size, "font-weight: bold;" if bold else ""))
    lab.setGeometry(x, y, w, h)
    return lab


# browser chrome + page furniture (all left-anchored or sidebar, like real)
ui_text("Watch: Language Video - YouTube - Chrome", 12, 6, 500, 22, 13)
ui_text("A Great Video About Words And Language", 10, 560, 560, 30, 19, True)
ui_text("1.2M views Â· 3 days ago Â· #language #learning", 10, 596, 420, 20, 12)
# a LIVE CHAT column: the messages churn constantly, like a stream
chat_lines = []
for i in range(5):
    chat_lines.append(ui_text("someone: first message %d here" % i,
                              886, 80 + i * 96, 370, 20, 13))
    ui_text("Channel %d Â· 800k views" % (i + 1), 886, 104 + i * 96, 250, 16, 11)

video = Video(browser)
video.setGeometry(10, 40, 850, 478)   # video pane LEFT of window centre

caption = QLabel("this is the FIRST line, on screen at lock-on", video)
caption.setStyleSheet("background: black; color: white; font-size: 22px;"
                      "font-weight: bold;")
caption.setAlignment(Qt.AlignCenter)
caption.setGeometry(215, 400, 420, 40)  # centred IN THE VIDEO, not the window
# NOTE: visible from the start, like real usage, where a caption is already
# on screen when the user picks the window. With no baseline pass it must be
# boxed by the FIRST scan, and the NEXT line (drawn over the same spot) must
# be re-read via the drift machinery.

boxes_layer = BoxLayer(browser)
boxes_layer.setGeometry(0, 0, 1272, 692)
boxes_layer.raise_()

pref = ctypes.c_int(1)
browser.show()
ctypes.windll.dwmapi.DwmSetWindowAttribute(
    int(browser.winId()), 33, ctypes.byref(pref), 4)
app.processEvents()

d = QApplication.primaryScreen().devicePixelRatio()
wl, wt, wr, wb = winapi.extended_frame_bounds(int(browser.winId()))
region = (wl, wt, wr - wl, wb - wt)
print("tracked region: %dx%d physical (dpr %.2f)"
      % (region[2], region[3], d))

events = []
got_fps = []
thread = QThread()
worker = CaptureWorker(lambda: region)
worker.moveToThread(thread)
thread.started.connect(worker.run)
live_now = []


def on_regions(payload):
    global live_now
    events.extend((k, b, time.perf_counter()) for k, b in payload[0])
    live_now = list(payload[1])
    boxes_layer.boxes = [tuple(v / d for v in s.box) for s in payload[1]]
    boxes_layer.update()


worker.regions.connect(on_regions)
worker.fps.connect(lambda v: got_fps.append(v))
thread.start()


chat_n = 0


def pump(seconds):
    """Video plays AND the chat column churns, like a real stream."""
    global chat_n
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        video.phase += 1
        video.update()
        if video.phase % 12 == 0:  # a new chat message every ~0.4s
            chat_n += 1
            chat_lines[chat_n % len(chat_lines)].setText(
                "viewer%d: message number %d lol" % (chat_n, chat_n))
        app.processEvents()
        time.sleep(0.03)


t0 = time.perf_counter()
while not got_fps and time.perf_counter() - t0 < 120:
    pump(0.1)
assert got_fps, "worker never came up"

cl, ct = (10 + 215) * d, (40 + 400) * d   # ground truth: caption geometry
cr, cb = cl + 420 * d, ct + 40 * d        # in window coords, physical px


def in_truth(b):
    return (b[0] >= cl - 24 and b[1] >= ct - 24
            and b[2] <= cr + 24 and b[3] <= cb + 24)


# phase 1: the FIRST caption line (present since lock-on) is boxed by the
# first scans — no baseline pass mutes it. Page text gets accepted too now;
# only the caption zone is asserted.
pump(3.0)
assert any(k == "appeared" and in_truth(b) for k, b, t in events), (
    "FAIL: the lock-on caption line was never accepted: %r" % events)
print("PASS: the lock-on caption line is boxed by the first scan")

# phase 2: the caption CHANGES to the next line (same spot, no clean vanish)
# -> the drift machinery must retire the old text and re-read the new
caption.setText("and here comes the second line")
t_on = time.perf_counter()
pump(3.0)
hits = [(b, t) for k, b, t in events
        if k == "appeared" and t >= t_on and in_truth(b)]
assert hits, "FAIL: changed caption line never re-read: %r" % events
box, t_hit = hits[0]
print("PASS: NEXT caption line re-read in place "
      "(latency %.0f ms, box %r)" % ((t_hit - t_on) * 1000, box))

# phase 3: caption clears -> no live box may remain IN THE CAPTION ZONE
# (page furniture stays live elsewhere — that's the accept-everything deal)
caption.hide()
t_off = time.perf_counter()
pump(2.0)
stale = [s.box for s in live_now if in_truth(s.box)]
assert not stale, "FAIL: stale live boxes after hide: %r" % stale
lat = [t - t_off for k, b, t in events
       if k == "cleared" and t >= t_off and in_truth(b)]
print("PASS: no live caption-zone boxes after hide (cleared %s)"
      % ("%.0f ms later" % (lat[0] * 1000) if lat else "before the hide"))

worker.stop()
thread.quit()
thread.wait(3000)
print("ALL PASS")
