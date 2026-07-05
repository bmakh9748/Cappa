"""Adversarial end-to-end test of the HYBRID detector on a simulated video,
running the real production CaptureWorker (background thread, neural scans).

The 'video': a panning blocky texture plus moving blobs at ~30 fps. Captions
are burned in the way subtitlers render them â€” outlined glyphs straight onto
the moving picture, NO background box. Scenarios, against ground truth:

  1. motion only, no caption            -> zero boxes (strict, no allowances)
  2. white text + black outline, bottom -> detect, box on text
  3. yellow text + outline, top third   -> detect (captions can be anywhere)
  4. scene freezes (still shot)         -> no false box from the freeze
  5. caption appearing on the still     -> detect
  6. white text, NO outline (hard)      -> informational
  7. small 16px text (hard)             -> informational
"""

import ctypes
import sys
import time

import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import Qt, QPointF, QThread
from PySide6.QtGui import (QPainter, QColor, QImage, QPixmap, QFont,
                           QFontMetricsF, QPainterPath, QPen)

from cappa import winapi
from cappa.detection.worker import CaptureWorker

W, H = 640, 360
rng = np.random.default_rng(7)


class VideoSim(QWidget):
    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        lo = rng.integers(40, 216, (45, 80, 3), dtype=np.uint8)
        tex = np.ascontiguousarray(
            np.repeat(np.repeat(lo, 16, axis=0), 16, axis=1))
        img = QImage(tex.data, 1280, 720, 3840, QImage.Format_RGB888)
        self._pm = QPixmap.fromImage(img.copy())
        self.tick = 0
        self.panning = True
        self.cap_path = None
        self.cap_color = None
        self.cap_outline = True
        self.det_boxes = []   # accepted boxes (logical px), drawn for display

    def advance(self):
        if self.panning:
            self.tick += 1

    def set_caption(self, text, px=28, color=(255, 255, 255),
                    outline=True, y_frac=0.88):
        if text is None:
            self.cap_path = None
            self.update()
            return None
        font = QFont("Segoe UI")
        font.setPixelSize(px)
        font.setBold(True)
        fm = QFontMetricsF(font)
        x = (W - fm.horizontalAdvance(text)) / 2
        path = QPainterPath()
        path.addText(QPointF(x, H * y_frac), font, text)
        self.cap_path = path
        self.cap_color = QColor(*color)
        self.cap_outline = outline
        self.update()
        r = path.boundingRect().adjusted(-3, -3, 3, 3)
        return (r.left(), r.top(), r.right(), r.bottom())

    def paintEvent(self, event):
        p = QPainter(self)
        p.drawPixmap(-int(self.tick * 4) % 640 - 640, 0, self._pm)
        p.drawPixmap(-int(self.tick * 4) % 640,
                     -int(self.tick * 2) % 360 - 360, self._pm)
        p.drawPixmap(-int(self.tick * 4) % 640,
                     -int(self.tick * 2) % 360, self._pm)
        p.setPen(Qt.NoPen)
        for i, col in enumerate((QColor(200, 80, 80), QColor(80, 120, 200))):
            x = (self.tick * (5 + 3 * i) + i * 260) % (W + 120) - 60
            p.setBrush(col)
            p.drawEllipse(QPointF(x, 90 + 110 * i), 45, 45)
        if self.cap_path is not None:
            p.setRenderHint(QPainter.Antialiasing)
            if self.cap_outline:
                p.strokePath(self.cap_path, QPen(QColor(0, 0, 0), 4))
            p.fillPath(self.cap_path, self.cap_color)
            # fullscreen-quality compression shimmer over the caption: the
            # watcher must not mistake this for the caption vanishing
            rect = self.cap_path.boundingRect()
            for _ in range(80):
                x = rect.x() + float(rng.random()) * rect.width()
                y = rect.y() + float(rng.random()) * rect.height()
                p.fillRect(int(x), int(y), 2, 2,
                           QColor(int(rng.integers(0, 256)),
                                  int(rng.integers(0, 256)),
                                  int(rng.integers(0, 256)), 80))
        # show what detection accepted. Outline only, drawn OUTSIDE the box:
        # this window is captured (unlike the real overlay), so ink must not
        # disturb the caption pixels the watcher guards.
        p.setPen(QPen(QColor(90, 210, 255), 2))
        p.setBrush(Qt.NoBrush)
        for l, t, r, b in self.det_boxes:
            p.drawRect(int(l) - 4, int(t) - 4, int(r - l) + 8, int(b - t) + 8)


app = QApplication(sys.argv)
sim = VideoSim()
sim.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
sim.setAttribute(Qt.WA_ShowWithoutActivating)
sim.setGeometry(200, 200, W, H)
pref = ctypes.c_int(1)
sim.show()
ctypes.windll.dwmapi.DwmSetWindowAttribute(
    int(sim.winId()), 33, ctypes.byref(pref), 4)
app.processEvents()

d = QApplication.primaryScreen().devicePixelRatio()
wl, wt, wr, wb = winapi.extended_frame_bounds(int(sim.winId()))
region = (wl, wt, wr - wl, wb - wt)

# ---- the real production worker, on its real background thread -------------
events = []      # (kind, box, t)
got_fps = []

thread = QThread()
worker = CaptureWorker(lambda: region)
worker.moveToThread(thread)
thread.started.connect(worker.run)
def on_regions(payload):
    events.extend((k, b, time.perf_counter()) for k, b in payload[0])
    sim.det_boxes = [tuple(v / d for v in s.box) for s in payload[1]]


worker.regions.connect(on_regions)
worker.fps.connect(lambda v: got_fps.append(v))
thread.start()

# let the model load and the loop spin up before the timeline starts
t0 = time.perf_counter()
while not got_fps and time.perf_counter() - t0 < 30:
    sim.advance()
    sim.update()
    app.processEvents()
    time.sleep(0.02)
assert got_fps, "worker never reported fps â€” did the model load fail?"

# ---- timeline (seconds), captions switched while the worker watches --------
CAPTIONS = [   # t_on, t_off, name, core?, kwargs
    (1.5, 4.0, "white+outline bottom", True,
     dict(text="The quick brown fox jumps tonight")),
    (4.8, 7.3, "yellow+outline top", True,
     dict(text="Something happened over here", color=(240, 210, 40),
          y_frac=0.18)),
    (9.0, 11.5, "caption on still shot", True,
     dict(text="A line during a frozen frame")),
    (12.5, 15.0, "white NO outline (hard)", False,
     dict(text="Plain white with no outline", outline=False)),
    (15.8, 18.3, "small 16px font (hard)", False,
     dict(text="tiny caption line for the test", px=16)),
]
FREEZE_AT, RESUME_AT, END = 8.0, 12.1, 19.5

windows = []
pending = list(CAPTIONS)
active = None
start = time.perf_counter()
while True:
    t = time.perf_counter() - start
    if t >= END:
        break
    if pending and active is None and t >= pending[0][0]:
        t_on, t_off, name, core, kw = pending.pop(0)
        truth = tuple(v * d for v in sim.set_caption(**kw))
        active = [name, core, truth, time.perf_counter(), None, t_off]
        windows.append(active)
    elif active is not None and t >= active[5]:
        sim.set_caption(None)
        active[4] = time.perf_counter()
        active = None
    sim.panning = not (FREEZE_AT <= t < RESUME_AT)
    sim.advance()
    sim.update()
    app.processEvents()
    time.sleep(0.025)

worker.stop()
thread.quit()
thread.wait(3000)

# ----------------------------------------------------------------- scorecard
def inside(box, truth, pad):
    return (box[0] >= truth[0] - pad and box[1] >= truth[1] - pad
            and box[2] <= truth[2] + pad and box[3] <= truth[3] + pad)


def covers_centre(box, truth):
    cx, cy = (truth[0] + truth[2]) / 2, (truth[1] + truth[3]) / 2
    return box[0] < cx < box[2] and box[1] < cy < box[3]


fails = []
matched = set()
for name, core, truth, t_on, t_off, _ in windows:
    hits = [(b, t) for k, b, t in events
            if k == "appeared" and t_on <= t <= t_off + 0.3
            and inside(b, truth, 30) and covers_centre(b, truth)]
    cleared = any(k == "cleared" and t_off <= t <= t_off + 1.2
                  for k, b, t in events)
    if hits:
        matched.update(t for _, t in hits)
        lat = (hits[0][1] - t_on) * 1000
        clr = "cleared ok" if cleared else "NO CLEAR"
        print("%-28s DETECTED  latency %4.0f ms  box %s  %s"
              % (name, lat, tuple(int(v) for v in hits[0][0]), clr))
        if core and not cleared:
            fails.append(name + " (no clear)")
    else:
        print("%-28s MISSED" % name)
        if core:
            fails.append(name)

in_window = lambda t: any(t_on <= t <= t_off + 0.3
                          for _, _, _, t_on, t_off, _ in windows)
false_pos = [(b, t) for k, b, t in events
             if k == "appeared" and t not in matched and not in_window(t)]
print("%-28s %s" % ("false positives",
                    "none" if not false_pos else repr(false_pos)))
if false_pos:
    fails.append("false positives")

print("CORE RESULT:", "ALL PASS" if not fails else "FAIL: %s" % fails)
sys.exit(1 if fails else 0)
