"""The transparent, always-on-top overlay that tracks a window or region.

Everything the user sees and interacts with in the video area lives here: the
tracking border, the click-through hit-testing, the window-pick and
region-select modes, and the follow loop that keeps the overlay glued to its
target. All low-level Win32 access is delegated to :mod:`cappa.winapi`."""

import time

from PySide6.QtWidgets import QApplication, QMainWindow
from PySide6.QtCore import Qt, QRect, QTimer, QThread
from PySide6.QtGui import QPainter, QColor, QPen, QCursor, QFont

from .. import winapi
from ..audio import LoopbackRecorder
from ..detection.worker import CaptureWorker
from ..flashcard import prefs as card_prefs
from ..source.bridge import BrowserBridge
from ..source.session import SourceSession
from .launcher import Launcher
from .word_popup import WordPopup

OFFSCREEN = -32000   # park the window here to hide it without destroying it
TICK_MS = 30
RECORDER_LINGER = 5.0   # bridge silent this long -> pause the audio recorder.
                        # The extension posts only from a VISIBLE YouTube tab
                        # (content.js skips document.hidden) and the bridge
                        # already holds reports for 5s, so the recorder stops
                        # ~10s after the user leaves the tab.
DOT_STALE = 2.0      # extension silent this long -> the yt dot goes dark.
                     # The content script posts ~700ms ticks from a VISIBLE
                     # tab, so three missed ticks means the tab was closed
                     # or hidden. The dot reacts here, near-instantly; the
                     # RECORDER keeps its longer linger below — stop/start
                     # churn on every tab flick is costly, a dark dot isn't.
CAPTION_RETRY_WAIT = 20.0  # cooldown between caption-fetch retries
CAPTION_RETRY_MAX = 2      # retries per video: transient failures (a bot
                           # check before the extension's cookies landed, a
                           # network blip) heal; a genuinely captionless
                           # video stops costing fetches after two tries
MIN_SELECTION = 20   # ignore accidental micro-drags (logical px)
EDGE_GRIP = 8        # grabbable band inside a locked region's border (logical px)

_EDGE_CURSORS = {
    "L": Qt.SizeHorCursor, "R": Qt.SizeHorCursor,
    "T": Qt.SizeVerCursor, "B": Qt.SizeVerCursor,
    "TL": Qt.SizeFDiagCursor, "BR": Qt.SizeFDiagCursor,
    "TR": Qt.SizeBDiagCursor, "BL": Qt.SizeBDiagCursor,
}


class OverlayWindow(QMainWindow):
    def __init__(self, on_settings=None, video_language=None):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)

        self._target_hwnd = None      # None => idle (nothing tracked)
        self._region = None           # (fl, ft, fr, fb) fractions of the window
        self._picking = False
        self._selecting = False
        self._instruction = ""
        self._tip_until = 0.0         # instruction shown as a timed tip
        self._sel_start = None        # QPoint, overlay-local logical px
        self._sel_cur = None
        self._sel_active = False      # left button held during a drag
        self._prev_lbutton = False
        self._click_through = True
        self._parked = False          # moved off-screen to hide
        self._resize_edges = ""       # edges being dragged: "T"/"BL"/…, "" = none
        self._hover_edges = ""        # edges under the cursor (drives the cursor)
        self._edge_prev_down = False
        self._base_status = ""        # status text before the fps suffix
        self._fps = 0.0
        self._captions = []           # live Sentences, boxes region-local physical px
        self._hover_word = None       # (QRect logical, Word) under the cursor
        self._word_prev_down = False  # LBUTTON edge detection for word clicks
        self._detector_ok = None      # None until the model load resolves
        self._src_status_shown = ""   # last source status pushed to the tooltip
        self._src_ready_tipped = False  # 'captions ready' announced for this video
        self._ext_version = ""        # extension version, as reported by it

        # The launcher is its own top-level window (screen corner), not a
        # child: it must not follow, park, or clip with the overlay.
        self.launcher = Launcher(self._start_pick, self._start_select,
                                 self._refresh_words, self._quit,
                                 on_set_video=self._use_clipboard_video,
                                 on_settings=on_settings,
                                 on_deselect=self._go_idle)
        # System-audio recorder: rolling buffer so a clicked word's clip is
        # already captured when they click. NOT unconditional (user call:
        # recording their system audio while they're off the YouTube tab is
        # not okay): once the extension has reported a visible YouTube tab,
        # _gate_recorder pauses capture whenever those reports stop, and
        # resumes when they're back. Sessions where the extension never
        # speaks keep the old always-on recorder — it's the only audio their
        # cards can get. Fail-soft — no device just means no audio.
        self._recorder = LoopbackRecorder()
        # Card audio OFF in settings leaves the whole video machinery
        # without a customer: don't record, don't auto-select videos, no
        # caption/audio downloads (the user's call: "if i'm not using audio
        # i really don't need you to track the video"). _poll_browser
        # re-reads the setting each tick, so the panel retunes it live.
        self._audio_off = not card_prefs.include("audio")
        if self._audio_off:
            self._recorder.error = ("recording off — card audio is "
                                    "disabled in settings")
        else:
            self._recorder.start()
        self._bridge_ever = False     # extension reported at least once
        self._bridge_lost_at = None   # when reports stopped (for the linger)
        self._recorder_paused = False # paused by the gate (not by errors)
        # The video language from Settings: caption-track preference AND which
        # rec model OCR reads captions with (Arabic needs its own pack).
        self._video_language = video_language
        # The active YouTube video: when it can align a caption line, cards get
        # exact caption-track audio instead of the loopback buffer. Fail-soft —
        # stays idle otherwise. The browser bridge (below) points it at whatever
        # YouTube tab is playing and feeds it the live playback position; the
        # launcher's clipboard action is the manual fallback.
        self._source = SourceSession(lang=video_language)
        self._bridge = BrowserBridge()
        self._bridge.start()
        self._source.set_position_provider(self._bridge.current)
        # Lets a card cut caption-exact audio from the loopback buffer when
        # the source download is missing (bot check, still in flight, ...).
        self._source.set_mono_mapper(self._bridge.mono_at)
        # ...and lets a position-matched card anchor its clip at the moment
        # the caption appeared on screen (mono -> video time).
        self._source.set_video_mapper(self._bridge.video_at)
        self._bridge_video_id = None      # last video the bridge auto-selected
        self._caption_retry = (None, 0, 0.0)  # (video, attempts, last try)
        if self._bridge.error:
            print("[cappa] browser bridge: " + self._bridge.error)
        self._popup = WordPopup(self, region_provider=self._card_region,
                                recorder=self._recorder, source=self._source,
                                captions_provider=lambda: list(self._captions))
        self._refresh_prev_down = False  # edge-detect the refresh hotkey

        # Keep our own border out of the frames the pipeline captures —
        # otherwise the diff sees our repaints and OCR would read our own UI.
        winapi.exclude_from_capture(int(self.winId()))

        self._go_idle()

        # WS_EX_TRANSPARENT eats all input, so a QShortcut would never fire. One
        # polling tick drives every hotkey, the pick/drag modes, the follow
        # loop, and the click-through hit-test — GetAsyncKeyState ignores focus.
        self._tick = QTimer(self)
        self._tick.timeout.connect(self._on_tick)
        self._tick.start(TICK_MS)

        self._start_capture()

    # ------------------------------------------------------------------ paint
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRect(1, 1, self.width() - 2, self.height() - 2)

        if self._picking:
            border, fill = QColor(80, 180, 255, 230), QColor(40, 120, 200, 22)
        elif self._selecting:
            border, fill = QColor(80, 180, 255, 230), QColor(8, 10, 16, 105)
        elif self._target_hwnd is not None:
            border, fill = QColor(92, 200, 132, 170), None
        else:
            return  # idle: the overlay is hidden, nothing to paint

        if fill is not None:
            painter.fillRect(rect, fill)

        # While dragging, cut a clear hole so the user sees the video underneath.
        if self._selecting and self._sel_start and self._sel_cur:
            sel = QRect(self._sel_start, self._sel_cur).normalized()
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            painter.fillRect(sel, Qt.transparent)
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            painter.setPen(QPen(QColor(90, 200, 255, 255), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(sel)

        painter.setPen(QPen(border, 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(rect, 7, 7)

        if self._region_resizable():
            self._draw_resize_handles(painter, rect, border)

        if (self._hover_word and self._target_hwnd is not None
                and not self._picking and not self._selecting):
            self._draw_word_highlight(painter)

        if self._instruction and (self._picking or self._selecting
                                  or time.time() < self._tip_until):
            self._draw_instruction(painter)

    def _draw_word_highlight(self, painter):
        """The word under the cursor, and nothing else: captions stay
        undecorated until the user reaches for one. Link-hover look: a
        barely-there lift over the word plus a crisp accent underline —
        clean, and readable over any video content. (A glyph-exact hue
        tint was tried and rejected: compression noise makes the stroke
        masks ragged on real video.)"""
        rect, _word = self._hover_word
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 30))
        painter.drawRoundedRect(rect, 6, 6)
        bar = QRect(rect.left() + 2, rect.bottom() - 2, rect.width() - 4, 3)
        painter.setBrush(QColor(90, 210, 255, 235))
        painter.drawRoundedRect(bar, 1, 1)

    def _draw_resize_handles(self, painter, rect, color):
        """Small grips at the corners and edge midpoints of a locked region —
        the cue that its border can be dragged like a window's."""
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(color.red(), color.green(), color.blue(), 235))
        xs = (rect.left(), rect.center().x(), rect.right())
        ys = (rect.top(), rect.center().y(), rect.bottom())
        for i, x in enumerate(xs):
            for j, y in enumerate(ys):
                if i == 1 and j == 1:
                    continue  # no handle in the middle of the region
                painter.drawRect(x - 3, y - 3, 6, 6)

    def _draw_instruction(self, painter):
        painter.setFont(QFont("Segoe UI", 11))
        fm = painter.fontMetrics()
        pad_x, pad_y = 18, 10
        bw = fm.horizontalAdvance(self._instruction) + pad_x * 2
        bh = fm.height() + pad_y * 2
        box = QRect((self.width() - bw) // 2, 28, bw, bh)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(18, 20, 28, 200))
        painter.drawRoundedRect(box, 10, 10)
        painter.setPen(QColor(235, 235, 240, 235))
        painter.drawText(box, Qt.AlignCenter, self._instruction)

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_click_through(True)

    # --------------------------------------------------------------- geometry
    def _dpr(self):
        return QApplication.primaryScreen().devicePixelRatio()

    def _apply_physical_geometry(self, left, top, right, bottom):
        """Win32 gives physical pixels; Qt geometry wants logical pixels.
        Divide by the device pixel ratio so the overlay lands on the window on
        scaled displays too. Only writes geometry when it actually moves."""
        d = self._dpr()
        g = QRect(
            round(left / d), round(top / d),
            round((right - left) / d), round((bottom - top) / d),
        )
        if g != self.geometry():
            self.setGeometry(g)

    def _go_idle(self):
        """No target: nothing to track, capture, or paint — the overlay hides
        and the launcher icon is the app's only presence on screen. (There is
        no 'watch the whole screen' mode: detection only ever runs on a
        picked window or a user-drawn area within it.)"""
        self._target_hwnd = None
        self._region = None
        self._picking = False
        self._selecting = False
        self._instruction = ""
        self._parked = False
        self._resize_edges = ""
        self._captions = []
        self._hover_word = None
        self._popup.hide()
        self._apply_edge_cursor("")
        self.launcher.show()
        self._set_status("Pick a window to start")
        self.hide()

    # ------------------------------------------------------------ window pick
    def _start_pick(self):
        """Cover the whole virtual desktop and stay click-through so
        root_window_at sees the window beneath the cursor, then wait for a left
        click to lock onto it."""
        self._picking = True
        self._region = None
        self._target_hwnd = None
        self._popup.hide()
        self._hover_word = None
        self._prev_lbutton = True  # ignore the click that pressed this button
        self._instruction = "Click the window to track   ·   Esc to cancel"
        self._apply_edge_cursor("")
        self.launcher.hide()  # can't be picked or clicked while we cover all
        vx, vy, vw, vh = winapi.virtual_screen_rect()
        self._apply_physical_geometry(vx, vy, vx + vw, vy + vh)
        self._apply_click_through(True)
        self._show()
        self.update()

    def _handle_pick_click(self):
        down = winapi.key_down(winapi.VK_LBUTTON)
        if down and not self._prev_lbutton:
            x, y = winapi.cursor_pos()
            root = winapi.root_window_at(x, y)
            if root and root != int(self.winId()):
                self._lock_to(root)
        self._prev_lbutton = down

    def _lock_to(self, hwnd):
        self._picking = False
        self._instruction = ""
        self._target_hwnd = hwnd
        self._captions = []
        self._hover_word = None
        self._popup.hide()
        self.launcher.show()
        self._show()  # idle keeps the overlay hidden; tracking needs it up
        self._set_status(f"Tracking: {self._title(hwnd)}")
        # Whole-window tracking is the weakest mode (page text everywhere,
        # captions small relative to the window): nudge toward the modes
        # that detect best. Shown for a few seconds, like pick/select help.
        self._instruction = ("Tip: fullscreen the video, or Select area "
                             "over it — captions detect much better")
        self._tip_until = time.time() + 6.0
        self.update()

    def _title(self, hwnd):
        t = winapi.window_title(hwnd)
        return t if len(t) <= 38 else t[:37] + "…"

    # ------------------------------------------------------------ region drag
    def _start_select(self):
        """Drag a box over the video/subtitle area. Stored as fractions of the
        tracked window so it follows both moves and resizes."""
        if self._target_hwnd is None:
            self._set_status("Pick a window first, then Select area")
            return
        self._selecting = True
        self._sel_start = self._sel_cur = None
        self._sel_active = False
        self._popup.hide()
        self._hover_word = None
        self._prev_lbutton = True  # ignore the click that pressed this button
        self._instruction = "Drag a box over the video / subtitle area   ·   Esc to cancel"
        self._resize_edges = ""
        self._apply_edge_cursor("")
        self.launcher.hide()  # keep the corner clear while dragging
        # Interactive (not click-through) so the drag doesn't scrub the video.
        self._apply_click_through(False)
        self.update()

    def _handle_select_drag(self):
        down = winapi.key_down(winapi.VK_LBUTTON)
        local = self.mapFromGlobal(QCursor.pos())
        if down and not self._prev_lbutton:
            self._sel_start = self._sel_cur = local
            self._sel_active = True
            self.update()
        elif down and self._sel_active:
            self._sel_cur = local
            self.update()
        elif (not down) and self._sel_active:
            self._sel_active = False
            self._finalize_selection(self._sel_start, local)
        self._prev_lbutton = down

    def _finalize_selection(self, a, b):
        sel = QRect(a, b).normalized()
        if sel.width() < MIN_SELECTION or sel.height() < MIN_SELECTION:
            self._cancel_select()
            return
        d = self._dpr()
        wl, wt, wr, wb = winapi.extended_frame_bounds(self._target_hwnd)
        ww, wh = max(wr - wl, 1), max(wb - wt, 1)

        def frac(logical_px, window_px):
            # overlay-local logical px -> physical px -> fraction of the window
            return min(max(logical_px * d / window_px, 0.0), 1.0)

        self._region = (
            frac(sel.left(), ww), frac(sel.top(), wh),
            frac(sel.right(), ww), frac(sel.bottom(), wh),
        )
        self._selecting = False
        self._instruction = ""
        self._captions = []  # region changed; boxes are stale coordinates
        self.launcher.show()
        self._set_status(f"Area locked in: {self._title(self._target_hwnd)}")
        self.update()

    def _cancel_select(self):
        self._selecting = False
        self._sel_active = False
        self._instruction = ""
        self.launcher.show()
        status = "Area locked" if self._region else \
            f"Tracking: {self._title(self._target_hwnd)}"
        self._set_status(status)
        self.update()

    # ------------------------------------------------------------ follow loop
    def _follow_target(self):
        hwnd = self._target_hwnd
        # Destroyed OR hidden both mean the user closed it: many apps (browser
        # popouts included) hide their panel on ✕ instead of destroying it.
        # Either way, deselect back to idle.
        if not winapi.is_window(hwnd) or not winapi.is_visible(hwnd):
            self._go_idle()
            self._set_status("Window closed — pick a window")
            return

        # Park off-screen (never hide/destroy) when the tracked window is
        # minimized or another app is in front, so we neither vanish for good
        # nor float over unrelated windows. Always-on-top targets (e.g. a
        # browser's picture-in-picture popout) stay visible without ever
        # owning the foreground, so focus loss doesn't park them.
        topmost = winapi.is_topmost(hwnd)
        # The launcher and its menu count as "ours": clicking them must not
        # read as the tracked window losing the foreground (=> park flicker).
        in_front = winapi.foreground_root() in (
            hwnd, int(self.winId()), *self.launcher.roots())
        if winapi.is_minimized(hwnd) or not (in_front or topmost):
            self._park()
            return
        self._unpark()

        # Clicking a topmost target raises it above us within the on-top
        # band; climb back so the tracking border stays visible.
        if topmost and not winapi.is_above(int(self.winId()), hwnd):
            winapi.raise_to_top(int(self.winId()))

        self._apply_physical_geometry(*self._target_bounds())

    def _target_bounds(self):
        """Physical-pixel (l, t, r, b) currently tracked: the whole window, or
        the locked sub-region as a fraction of it. Assumes a valid target."""
        wl, wt, wr, wb = winapi.extended_frame_bounds(self._target_hwnd)
        if not self._region:
            return wl, wt, wr, wb
        fl, ft, fr, fb = self._region
        ww, wh = wr - wl, wb - wt
        return wl + fl * ww, wt + ft * wh, wl + fr * ww, wt + fb * wh

    def _park(self):
        if self._parked:
            return
        self._parked = True
        self._popup.hide()
        self._hover_word = None
        self._apply_click_through(True)
        self.setGeometry(OFFSCREEN, OFFSCREEN, 10, 10)

    def _unpark(self):
        self._parked = False

    # ---------------------------------------------------------- region resize
    def _region_resizable(self):
        return (self._region is not None and self._target_hwnd is not None
                and not self._parked and not self._picking
                and not self._selecting)

    def _handle_region_resize(self):
        """Window-style resizing for a locked region: grab the border (an
        EDGE_GRIP band inside it) and drag. Polling-based like the other
        modes; the bands are interactive rects, so click-through lifts there
        and the video stays clickable everywhere else. This is also the
        escape hatch when a selection came out accidentally tiny."""
        down = winapi.key_down(winapi.VK_LBUTTON)
        if self._resize_edges:
            if down:
                self._drag_region_edges()
            else:
                self._resize_edges = ""  # released — drag over
        else:
            local = self.mapFromGlobal(QCursor.pos())
            hover = self._edge_hit(local)
            self._apply_edge_cursor(hover)
            if hover and down and not self._edge_prev_down:
                self._resize_edges = hover
        self._edge_prev_down = down

    def _edge_hit(self, local):
        """Which edges the point can grab: "", "T", "BL", …"""
        w, h = self.width(), self.height()
        if not (0 <= local.x() < w and 0 <= local.y() < h):
            return ""
        top, bottom = local.y() < EDGE_GRIP, local.y() >= h - EDGE_GRIP
        if top and bottom:  # region thinner than two bands: nearer edge wins
            top = local.y() * 2 <= h
            bottom = not top
        left, right = local.x() < EDGE_GRIP, local.x() >= w - EDGE_GRIP
        if left and right:
            left = local.x() * 2 <= w
            right = not left
        return (("T" if top else "B" if bottom else "")
                + ("L" if left else "R" if right else ""))

    def _apply_edge_cursor(self, edges):
        if edges == self._hover_edges:
            return
        self._hover_edges = edges
        if edges:
            self.setCursor(_EDGE_CURSORS[edges])
        else:
            self.unsetCursor()

    def _drag_region_edges(self):
        """Move the grabbed edges to the cursor, clamped inside the tracked
        window and to a minimum size so the region can't collapse."""
        x, y = winapi.cursor_pos()  # physical px, like the window bounds
        wl, wt, wr, wb = winapi.extended_frame_bounds(self._target_hwnd)
        l, t, r, b = self._target_bounds()
        m = MIN_SELECTION * self._dpr()
        e = self._resize_edges
        if "L" in e:
            l = min(max(x, wl), r - m)
        if "R" in e:
            r = max(min(x, wr), l + m)
        if "T" in e:
            t = min(max(y, wt), b - m)
        if "B" in e:
            b = max(min(y, wb), t + m)
        ww, wh = max(wr - wl, 1), max(wb - wt, 1)
        self._region = ((l - wl) / ww, (t - wt) / wh,
                        (r - wl) / ww, (b - wt) / wh)
        self._apply_physical_geometry(l, t, r, b)
        self.update()  # keep the handles glued to the moving border

    # -------------------------------------------------------- click-through
    def _apply_click_through(self, enabled):
        winapi.set_click_through(int(self.winId()), enabled)
        self._click_through = enabled

    def _interactive_rects(self):
        """Regions that should capture the mouse: the word hotspots, the
        open popup, and a locked region's edge bands. (The launcher is its
        own window and receives clicks natively — nothing to route here.)"""
        rects = [entry[0] for entry in self._word_rects()]
        if self._popup.isVisible():
            rects.append(self._popup.geometry())
        if self._region_resizable():
            w, h, g = self.width(), self.height(), EDGE_GRIP
            rects += [QRect(0, 0, w, g), QRect(0, h - g, w, g),
                      QRect(0, 0, g, h), QRect(w - g, 0, g, h)]
        return rects

    # ------------------------------------------------------------ word click
    def _word_rects(self):
        """The clickable hotspots: [(QRect logical, Word)] across the live
        captions. Word boxes arrive in region-local physical px, same space
        as the caption boxes."""
        if (self._target_hwnd is None or self._parked or self._picking
                or self._selecting):
            return []
        d = self._dpr()
        pad = 2  # a little slack around the glyphs
        rects = []
        for sentence in self._captions:
            for word in sentence.words:
                l, t, r, b = word.box
                rects.append((QRect(round(l / d) - pad, round(t / d) - pad,
                                    round((r - l) / d) + 2 * pad,
                                    round((b - t) / d) + 2 * pad), word))
        return rects

    def _handle_words(self):
        """Highlight the word under the cursor; a click on it opens the
        popup. Polling-based like the app's other modes — the overlay may
        have been click-through a tick earlier, so Qt press events aren't
        reliable here."""
        local = self.mapFromGlobal(QCursor.pos())
        hover = None
        if not (self._resize_edges or self._hover_edges
                or (self._popup.isVisible()
                    and self._popup.geometry().contains(local))):
            for rect, word in self._word_rects():
                if rect.contains(local):
                    hover = (rect, word)
                    break
        if (hover is None) != (self._hover_word is None) or (
                hover is not None and hover[0] != self._hover_word[0]):
            self._hover_word = hover
            if not self._hover_edges:
                if hover is not None:
                    self.setCursor(Qt.PointingHandCursor)
                else:
                    self.unsetCursor()
            self.update()

        down = winapi.key_down(winapi.VK_LBUTTON)
        if down and not self._word_prev_down and self._hover_word is not None:
            self._popup.show_for(self._hover_word[1], self._hover_word[0])
        self._word_prev_down = down

    def _update_click_through(self):
        """Flip off click-through only while the cursor is over an interactive
        region, so the rest of the overlay stays transparent to clicks and the
        browser behind it works normally."""
        if self._parked or self._picking or self._selecting:
            return  # those modes own the click-through state
        if self._resize_edges:
            return  # keep click-through OFF so Windows keeps routing the drag
        local = self.mapFromGlobal(QCursor.pos())
        over = any(r.contains(local) for r in self._interactive_rects())
        if over == self._click_through:  # over => want click-through OFF
            self._apply_click_through(not over)

    # ---------------------------------------------------------- visibility
    def _show(self):
        if not self.isVisible():
            self.show()

    # ------------------------------------------------------------- status
    def _set_status(self, text):
        self._base_status = text
        self._render_status()

    def _render_status(self):
        capturing = (self._target_hwnd is not None and not self._parked
                     and not self._picking and not self._selecting)
        if capturing and self._fps > 0:
            text = f"{self._base_status}   ·   {self._fps:.0f} fps"
            if self._detector_ok is False:
                text += "   ·   ⚠ text detection OFFLINE"
            else:
                # Always show the count while tracking: "0 captions" means
                # alive-and-looking, which reads very differently from silence.
                n = len(self._captions)
                text += f"   ·   {n} caption" + ("" if n == 1 else "s")
        else:
            text = self._base_status
        if self._audio_off:
            text += "   ·   card audio off"
        elif self._source.status != "idle":
            text += "   ·   yt: " + self._source.status
        elif self._bridge.error:
            # A dead bridge otherwise looks exactly like "no YouTube open"
            # (the port collision with AnkiConnect hid behind that for days).
            text += "   ·   ⚠ yt bridge down: port in use?"
        if self._ext_version:
            text += "   ·   ext " + self._ext_version
        self.launcher.set_status(text)
        self.launcher.set_state(self._target_hwnd is not None,
                                self._detector_ok, self._yt_light())

    def _yt_light(self):
        """The launcher's caption-source dot: 'ready' (green) = caption track
        usable for cards, 'loading' (amber) = fetch in flight, 'error' (red)
        = this video has no usable track, None (dark) = no video yet — or
        nothing is WATCHING one: the YouTube tab closed (the recorder gate's
        signal) or card audio is off. A green dot must mean "the next card
        gets caption-exact audio", which is a lie once the tab is gone."""
        if self._audio_off or self._recorder_paused:
            return None
        age = self._bridge.age()
        if age is not None and age > DOT_STALE:
            return None   # tab just closed/hidden: dark within ~2s
        if self._source.transcript_ready:
            return "ready"
        status = self._source.status
        if status == "idle":
            return None
        if status == "loading captions":
            return "loading"
        return "error"

    def _announce_source_status(self):
        """The launcher tooltip only shows on hover, so caption-track progress
        was invisible — pop the overlay tip on the transitions that matter:
        the track becoming usable, or this video not having one."""
        if self._source.transcript_ready:
            if not self._src_ready_tipped:
                self._src_ready_tipped = True
                self._show_tip(
                    "YouTube captions ready — cards get caption-exact audio")
        else:
            self._src_ready_tipped = False
            if self._source.status in ("no captions", "bad URL"):
                self._show_tip("YouTube captions unavailable (%s) — card "
                               "audio falls back to what just played"
                               % self._source.status)

    # ------------------------------------------------------------- capture
    def _start_capture(self):
        """Spin up the background capture thread. It calls capture_region() from
        its own thread each frame to learn what to grab; results (fps now,
        detections later) come back via queued signals on the UI thread."""
        self._capture_thread = QThread(self)
        self._capture_worker = CaptureWorker(
            self.capture_region,
            # Read off-thread at scan time (atomic attribute read, same deal
            # as capture_region): a user-drawn area is judged from the first
            # scan, so a paused video's on-screen caption is found instantly.
            user_area_provider=lambda: self._region is not None,
            # Experiment (user call): too many real caption words were being
            # rejected — sometimes between consecutive lines — and hover-only
            # styling makes loose detection cheap. Every text line the
            # detector finds becomes hoverable words; junk text is hoverable
            # too, nothing more. Flip to False to restore the caption gates.
            accept_all=True,
            # The video language from Settings decides which rec model reads
            # captions (Arabic etc. need their own pack; None = default).
            ocr_lang=self._video_language,
        )
        self._capture_worker.moveToThread(self._capture_thread)
        self._capture_thread.started.connect(self._capture_worker.run)
        self._capture_worker.fps.connect(self._on_capture_fps)
        self._capture_worker.regions.connect(self._on_regions)
        self._capture_worker.detector_ok.connect(self._on_detector_ok)
        self._capture_thread.start()
        QApplication.instance().aboutToQuit.connect(self._stop_capture)

    def _stop_capture(self):
        self._capture_worker.stop()
        self._capture_thread.quit()
        self._capture_thread.wait(1000)
        self._recorder.stop()
        self._bridge.stop()

    def _card_region(self):
        """The tracked area to screenshot for a flashcard: physical
        (left, top, width, height), or None when nothing's grabbable. Same
        rect the capture worker uses, so the shot matches what was detected.
        Called from the popup's card thread — atomic reads only."""
        return self.capture_region()

    def _on_capture_fps(self, fps):
        self._fps = fps
        self._render_status()

    def _on_detector_ok(self, ok):
        self._detector_ok = ok
        self._render_status()

    def _on_regions(self, payload):
        """Captions appeared or cleared: refresh the hotspots and the count
        in the launcher tooltip. Payload is (events, live_captions) where
        live_captions is [(box, words)] — it fully replaces what we had."""
        events, captions = payload
        self._captions = captions
        self._render_status()
        self.update()

    def capture_region(self):
        """(left, top, width, height) physical pixels for the capture thread to
        grab, or None when there's nothing worth capturing. Called off-thread —
        keep it to atomic attribute reads plus thread-safe Win32 calls."""
        hwnd = self._target_hwnd
        if (hwnd is None or self._parked or self._picking or self._selecting
                or self._resize_edges):  # region in flux mid-drag: wait
            return None
        if not winapi.is_window(hwnd):
            return None
        left, top, right, bottom = self._target_bounds()
        width, height = int(round(right - left)), int(round(bottom - top))
        if width < 2 or height < 2:
            return None
        return int(round(left)), int(round(top)), width, height

    # ---------------------------------------------------------------- exit
    def _quit(self):
        QApplication.quit()

    def _quit_combo_down(self):
        # Three modifiers on purpose: Ctrl+Shift+X alone is bound in browsers
        # (user-reported collision), and this app lives OVER a browser.
        return (winapi.key_down(winapi.VK_CONTROL)
                and winapi.key_down(winapi.VK_MENU)
                and winapi.key_down(winapi.VK_SHIFT)
                and winapi.key_down(winapi.VK_X))

    # ------------------------------------------------------------- refresh
    def _refresh_combo_down(self):
        # Ctrl+Alt+Shift+R: same triple-modifier scheme as quit, so nothing
        # in a browser collides with it. Re-scans the tracked region.
        return (winapi.key_down(winapi.VK_CONTROL)
                and winapi.key_down(winapi.VK_MENU)
                and winapi.key_down(winapi.VK_SHIFT)
                and winapi.key_down(winapi.VK_R))

    def _refresh_words(self):
        """Force the worker to drop its detection memory and re-scan now —
        the launcher's 'Refresh words' and the Ctrl+Alt+Shift+R hotkey. A
        no-op when nothing is tracked (there's no region to scan). The open
        popup keeps its word until the fresh hotspots land."""
        if self._target_hwnd is None:
            return
        self._capture_worker.refresh()

    # ------------------------------------------------------------ yt source
    def _poll_browser(self):
        """Auto-select the video the browser extension reports, and gate the
        audio recorder on those reports arriving at all (_gate_recorder). A
        no-op when the extension isn't installed / the bridge is down
        (current() -> None) or the video hasn't changed, so the manual
        clipboard path still works. All of it stands down while card audio
        is off in the settings — nothing would use what it gathers."""
        off = not card_prefs.include("audio")
        if off != self._audio_off:
            self._audio_off = off
            if off:
                self._bridge_video_id = None   # re-select on re-enable
                self._bridge_lost_at = None
                self._recorder.stop()
                self._recorder.error = ("recording off — card audio is "
                                        "disabled in settings")
            elif not self._recorder_paused:
                self._recorder.start()
        if self._audio_off:
            return
        state = self._bridge.current()
        self._gate_recorder(alive=state is not None)
        if not state:
            return
        self._ext_version = state.get("ext") or self._ext_version
        vid = state.get("videoId")
        if vid and vid != self._bridge_video_id:
            self._bridge_video_id = vid
            self._source.set_video(state.get("url") or vid)
            print("[cappa] source: browser video %s (%s)"
                  % (vid, (state.get("title") or "?")[:50]))
        elif vid and self._source.status == "no captions":
            self._retry_captions(vid, state)

    def _retry_captions(self, vid, state):
        """A caption fetch that failed can be transient (bot check before the
        extension's cookies arrived, a network blip) — but it used to be
        PERMANENT: the session refused the same video and the poll never
        re-selected it, so the dot sat red for the whole watch unless the
        user changed videos (which is why it kept coming back on tab
        switches). Retry a couple of times with a cooldown instead."""
        r_vid, tries, at = self._caption_retry
        if r_vid != vid:
            r_vid, tries, at = vid, 0, 0.0
        now = time.monotonic()
        if tries >= CAPTION_RETRY_MAX or now - at < CAPTION_RETRY_WAIT:
            self._caption_retry = (r_vid, tries, at)
            return
        self._caption_retry = (vid, tries + 1, now)
        print("[cappa] source: retrying captions for %s (%d/%d)"
              % (vid, tries + 1, CAPTION_RETRY_MAX))
        self._source.set_video(state.get("url") or vid)

    def _gate_recorder(self, alive):
        """Record system audio only while the extension is reporting a
        visible YouTube tab. `alive` is this tick's bridge freshness. Only
        ever arms itself once the extension has spoken (_bridge_ever), so a
        session without the extension records continuously as before; after
        that, reports stopping for RECORDER_LINGER means the user left the
        tab — stop capturing until they're back. Cards built while paused
        note the recorder's `error` instead of getting someone-else's-audio
        clips."""
        if alive:
            self._bridge_ever = True
            self._bridge_lost_at = None
            if self._recorder_paused:
                self._recorder_paused = False
                self._recorder.start()
            return
        if not self._bridge_ever or self._recorder_paused:
            return
        now = time.monotonic()
        if self._bridge_lost_at is None:
            self._bridge_lost_at = now
        elif now - self._bridge_lost_at >= RECORDER_LINGER:
            self._recorder_paused = True
            self._recorder.stop()
            self._recorder.error = "recording paused — no YouTube tab in sight"

    def set_video_language(self, lang):
        """Apply a new video language from Settings: caption tracks fetched
        from now on prefer it (the current video keeps its captions), and the
        OCR reader swaps to that script's rec model and re-scans. Translation
        source is set separately."""
        self._video_language = lang
        self._source.set_language(lang)
        self._capture_worker.set_ocr_language(lang)

    def _use_clipboard_video(self):
        """Point the source session at the YouTube video whose URL is on the
        clipboard. Cards made afterwards get exact caption-track timing/audio;
        fetching happens in the background. A later stage feeds this from the
        browser automatically."""
        from ..source.youtube import SourceError, extract_video_id
        text = (QApplication.clipboard().text() or "").strip()
        try:
            vid = extract_video_id(text)
        except SourceError:
            self._show_tip("Copy a YouTube video URL first, then try again")
            return
        self._source.set_video(text)
        print("[cappa] source: loading %s" % vid)
        self._show_tip("Loading captions for %s ..." % vid)
        self._render_status()

    def _show_tip(self, text, seconds=4.0):
        self._instruction = text
        self._tip_until = time.time() + seconds
        self.update()

    # ---------------------------------------------------------------- tick
    def _on_tick(self):
        # Ctrl+Alt+Shift+X quits from anywhere (won't fire while typing).
        if self._quit_combo_down():
            self._quit()
            return

        # Ctrl+Alt+Shift+R re-scans the tracked region. Edge-detected so
        # holding the combo fires exactly one refresh, not one per tick.
        refresh_down = self._refresh_combo_down()
        if refresh_down and not self._refresh_prev_down:
            self._refresh_words()
        self._refresh_prev_down = refresh_down

        # Follow whatever YouTube video the browser reports, and reflect the
        # background caption-fetch progress in the launcher tooltip promptly
        # (even while idle, when no fps events are firing).
        self._poll_browser()
        if self._source.status != self._src_status_shown:
            self._src_status_shown = self._source.status
            self._announce_source_status()
            self._render_status()

        # The lock-on tip expires on its own.
        if self._tip_until and time.time() >= self._tip_until:
            self._tip_until = 0.0
            if not (self._picking or self._selecting):
                self._instruction = ""
            self.update()

        # Escape only cancels a pending pick/drag — it never closes the app, so
        # it stays free for YouTube's minimize-video shortcut.
        esc = winapi.key_down(winapi.VK_ESCAPE)

        if self._picking:
            if esc:
                self._go_idle()
            else:
                self._handle_pick_click()
            return
        if self._selecting:
            if esc:
                self._cancel_select()
            else:
                self._handle_select_drag()
            return

        if self._target_hwnd is not None:
            self._follow_target()
            if self._region_resizable():
                self._handle_region_resize()
            if self._target_hwnd is not None and not self._parked:
                self._handle_words()

        self._update_click_through()
