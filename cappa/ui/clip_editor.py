"""The preview's audio/sentence edit strip.

One widget: the clip's neighbourhood as a waveform (so silence and speech
are visible), the audio window as a draggable range over it, and under the
wave a lane of the words the editor timed (the card's own sentence bright,
the neighbours dim), with the sentence's own span dragging word by word.
A link button couples the two ranges; a play button plays the current cut.

This is paint-and-mouse only: every number it shows comes from a
flashcard.edit.DraftEditor (whose queries are pure in-memory), and every
change leaves as a signal -- the preview window decides what a drag means
(re-cut, regrow, re-translate). Emits *Edited(..., final=False) while a
drag is live so labels can follow word by word, and final=True on release,
which is when anything touches disk or network."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
                               QWidget)

STRIP_WIDTH = 400     # matches the preview's text width
WAVE_H = 46           # the waveform band
LANE_H = 30           # the word lane under it
PAD = 8               # left/right inset so edge handles stay grabbable
GRIP = 6              # px within which an edge handle answers the mouse

_WAVE_IN = QColor(90, 210, 255, 210)     # bars inside the audio range
_WAVE_OUT = QColor(90, 210, 255, 60)     # bars outside it
_SEL_FILL = QColor(90, 210, 255, 26)
_SEL_EDGE = QColor(90, 210, 255, 230)
_SPAN_FILL = QColor(124, 233, 168, 30)   # the sentence span, green family
_SPAN_EDGE = QColor(124, 233, 168, 230)
_WORD_CORE = QColor(234, 234, 240)
_WORD_NEAR = QColor(127, 132, 150)
_WORD_CLICKED = QColor(90, 210, 255)
_TICK = QColor(255, 255, 255, 46)


class ClipEditor(QWidget):
    """Header row (play · duration · link) over the painted strip."""

    rangeEdited = Signal(float, float, bool)   # audio t0, t1, final
    spanEdited = Signal(int, int, bool)        # word span i, j, final
    playClicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._play = QPushButton("▶  Play", self)
        self._play.setObjectName("play")
        self._play.setCursor(Qt.PointingHandCursor)
        self._play.clicked.connect(self.playClicked)
        self._length = QLabel("", self)
        self._length.setObjectName("fieldName")
        # Linked is the point of the strip (audio range <-> sentence follow
        # each other); unlink to move one without the other. A manual text
        # edit unlinks too -- typed words must not be slid away.
        self._link = QPushButton("🔗 linked", self)
        self._link.setObjectName("play")
        self._link.setCursor(Qt.PointingHandCursor)
        self._link.setCheckable(True)
        self._link.setChecked(True)
        self._link.toggled.connect(self._link_changed)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        head.addWidget(self._play)
        head.addWidget(self._length)
        head.addStretch(1)
        head.addWidget(self._link)

        self._strip = _Strip(self)
        self._strip.rangeEdited.connect(self.rangeEdited)
        self._strip.spanEdited.connect(self.spanEdited)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addLayout(head)
        lay.addWidget(self._strip)
        self.setMinimumWidth(STRIP_WIDTH)

    def attach(self, editor, clicked):
        """Point the strip at a prepared DraftEditor. `clicked` is the global
        index of the studied word's chip in editor.words (-1 unknown) --
        editor.clicked_word_index finds it by text, robust for CJK."""
        self._strip.set_state(editor.envelope, editor.clamp_selection,
                              editor.words, (editor.ws_start, editor.ws_end),
                              editor.sel, editor.span, clicked)
        self.set_duration(editor.sel[1] - editor.sel[0])

    def lock_span(self):
        """Stop the sentence span from being dragged: after a hand-typed
        sentence the word timeline no longer matches the text, so sliding it
        would silently revert the typed words."""
        self._strip.set_span_locked(True)

    def linked(self):
        return self._link.isChecked()

    def set_linked(self, on):
        self._link.setChecked(bool(on))

    def set_duration(self, seconds):
        self._length.setText("%.1f s" % max(0.0, seconds))

    def set_selection(self, t0, t1):
        self._strip.set_sel(t0, t1)
        self.set_duration(t1 - t0)

    def set_span(self, i, j):
        self._strip.set_span(i, j)

    def _link_changed(self, on):
        self._link.setText("🔗 linked" if on else "⛓ separate")


class _Strip(QWidget):
    """The painted timeline: waveform + audio range on top, word lane +
    sentence span below. Pure view -- state arrives via set_state and the
    setters, edits leave as signals."""

    rangeEdited = Signal(float, float, bool)
    spanEdited = Signal(int, int, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(WAVE_H + LANE_H)
        self.setMouseTracking(True)
        self._env = None          # bins -> [0..1] peaks (editor.envelope)
        self._clamp = None        # (t0, t1) -> legal (t0, t1)
        self._words = []
        self._ws = (0.0, 1.0)
        self._sel = (0.0, 1.0)
        self._span = (0, -1)
        self._clicked = -1
        self._span_locked = False  # sentence typed by hand -> span frozen
        self._drag = None         # 'a0' | 'a1' | 'pan' | 's0' | 's1'
        self._drag_t = 0.0        # grab offset for 'pan'

    def set_state(self, env, clamp, words, ws, sel, span, clicked):
        self._env = env
        self._clamp = clamp
        self._words = words
        self._ws = ws
        self._sel = tuple(sel)
        self._span = tuple(span)
        self._clicked = clicked
        self.update()

    def set_sel(self, t0, t1):
        self._sel = (t0, t1)
        self.update()

    def set_span(self, i, j):
        self._span = (i, j)
        self.update()

    def set_span_locked(self, on):
        self._span_locked = bool(on)
        self.update()

    # ------------------------------------------------------------ geometry
    def _x(self, t):
        w0, w1 = self._ws
        span = max(w1 - w0, 1e-6)
        return PAD + (t - w0) / span * (self.width() - 2 * PAD)

    def _t(self, x):
        w0, w1 = self._ws
        return w0 + (x - PAD) / max(self.width() - 2 * PAD, 1) * (w1 - w0)

    def _mid_x(self, i):
        w = self._words[i]
        return self._x((w["start"] + w["end"]) / 2.0)

    # -------------------------------------------------------------- paint
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        self._paint_wave(p)
        self._paint_lane(p)
        p.end()

    def _paint_wave(self, p):
        if self._env is None:
            return
        inner = self.width() - 2 * PAD
        if inner <= 0:
            return
        bins = max(inner // 3, 16)      # a 3px column per peak reads clean
        peaks = self._env(bins)
        mid = WAVE_H / 2.0
        x0, x1 = self._x(self._sel[0]), self._x(self._sel[1])
        p.fillRect(int(x0), 0, max(int(x1 - x0), 1), WAVE_H, _SEL_FILL)
        for b, peak in enumerate(peaks):
            x = PAD + b * inner / bins
            h = max(peak * (mid - 3), 1.0)
            color = _WAVE_IN if x0 <= x <= x1 else _WAVE_OUT
            p.fillRect(int(x), int(mid - h), 2, int(2 * h), color)
        pen = QPen(_SEL_EDGE, 2)
        p.setPen(pen)
        for x in (x0, x1):
            p.drawLine(int(x), 2, int(x), WAVE_H - 2)
            p.setBrush(_SEL_EDGE)
            p.drawEllipse(int(x) - 3, WAVE_H // 2 - 3, 6, 6)

    def _paint_lane(self, p):
        top = WAVE_H
        base = top + LANE_H - 8
        i0, j0 = self._span
        if self._words and 0 <= i0 <= j0 < len(self._words):
            xa = self._x(self._words[i0]["start"])
            xb = self._x(self._words[j0]["end"])
            p.fillRect(int(xa), top + 2, max(int(xb - xa), 2), LANE_H - 6,
                       _SPAN_FILL)
            pen = QPen(_SPAN_EDGE, 2)
            p.setPen(pen)
            for x in (xa, xb):
                p.drawLine(int(x), top + 2, int(x), top + LANE_H - 4)
        font = p.font()
        font.setPixelSize(10)
        p.setFont(font)
        metrics = p.fontMetrics()
        last_right = -1e9
        for i, w in enumerate(self._words):
            x = self._mid_x(i)
            p.setPen(QPen(_TICK, 1))
            p.drawLine(int(x), base + 4, int(x), base + 7)
            text = w["text"]
            tw = metrics.horizontalAdvance(text)
            left = x - tw / 2.0
            if left < last_right + 4:
                continue          # too dense to label; the tick still shows
            last_right = left + tw
            if i == self._clicked:
                color = _WORD_CLICKED
            elif w["kind"] == "core":
                color = _WORD_CORE
            else:
                color = _WORD_NEAR
            p.setPen(QPen(color))
            p.drawText(int(left), base, text)

    # -------------------------------------------------------------- mouse
    def _hit(self, pos):
        """What the press grabbed: audio edges first (they sit on the wave),
        then the span edges on the lane, then a pan inside the range."""
        x, y = pos.x(), pos.y()
        if y <= WAVE_H:
            x0, x1 = self._x(self._sel[0]), self._x(self._sel[1])
            if abs(x - x0) <= GRIP:
                return "a0"
            if abs(x - x1) <= GRIP:
                return "a1"
            if x0 < x < x1:
                return "pan"
            return None
        if self._span_locked:
            return None
        i0, j0 = self._span
        if self._words and 0 <= i0 <= j0 < len(self._words):
            xa = self._x(self._words[i0]["start"])
            xb = self._x(self._words[j0]["end"])
            if abs(x - xa) <= GRIP:
                return "s0"
            if abs(x - xb) <= GRIP:
                return "s1"
        return None

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        self._drag = self._hit(event.position())
        if self._drag == "pan":
            self._drag_t = self._t(event.position().x()) - self._sel[0]

    def mouseMoveEvent(self, event):
        if self._drag is None:
            kind = self._hit(event.position())
            self.setCursor(Qt.SizeHorCursor if kind in ("a0", "a1", "s0",
                                                        "s1")
                           else Qt.OpenHandCursor if kind == "pan"
                           else Qt.ArrowCursor)
            return
        t = self._t(event.position().x())
        if self._drag in ("a0", "a1", "pan"):
            t0, t1 = self._sel
            if self._drag == "a0":
                t0 = min(t, t1)
            elif self._drag == "a1":
                t1 = max(t, t0)
            else:
                width = t1 - t0
                t0 = t - self._drag_t
                t1 = t0 + width
            if self._clamp is not None:
                t0, t1 = self._clamp(t0, t1)
            if (t0, t1) != self._sel:
                self._sel = (t0, t1)
                self.update()
                self.rangeEdited.emit(t0, t1, False)
        else:
            i0, j0 = self._span
            near = self._nearest_word(t)
            if near is None:
                return
            if self._drag == "s0":
                i0 = min(near, j0)
            else:
                j0 = max(near, i0)
            if (i0, j0) != self._span:
                self._span = (i0, j0)
                self.update()
                self.spanEdited.emit(i0, j0, False)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or self._drag is None:
            return
        drag, self._drag = self._drag, None
        if drag in ("a0", "a1", "pan"):
            self.rangeEdited.emit(self._sel[0], self._sel[1], True)
        else:
            self.spanEdited.emit(self._span[0], self._span[1], True)

    def _nearest_word(self, t):
        """The word whose spoken midpoint is nearest to time t, or None."""
        if not self._words:
            return None
        return min(range(len(self._words)),
                   key=lambda i: abs((self._words[i]["start"]
                                      + self._words[i]["end"]) / 2.0 - t))
