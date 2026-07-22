"""The Parsec-style launcher: a small translucent icon parked at the
bottom-left of the primary screen. Clicking it pops the app's menu — Pick
window / Select area / Refresh words / Exit — and hovering shows the current
status (tracking target, fps, caption count) as its tooltip. A coloured dot
on the icon mirrors the app state: green = tracking, grey = idle, red = text
detection failed to load.

'Refresh words' (and its Ctrl+Alt+Shift+R hotkey) forces the detector to
re-scan the tracked region from scratch — the deliberate way to make it look
again without nudging the window size. Enabled only while tracking.

It replaces the old in-overlay control bar and is a top-level window of its
own — NOT a child of the overlay — so it neither follows nor parks with the
tracked window: it is always reachable. It never takes focus
(WindowDoesNotAcceptFocus), so clicking it can't pull the foreground off the
tracked window and park the overlay mid-click; the overlay also whitelists
roots() in its foreground check for the menu's sake. Both windows are
excluded from screen capture, so they never pollute captured frames even
when the tracked region covers this corner of the screen.

The icon is the Cappa logo (ui/logo.py — the 1b "Caption tile"), painted
live so hover can fade the tile. The state dots sit on small dark discs
because two of their colours are red-on-red against the tile."""

from PySide6.QtWidgets import QApplication, QMenu, QWidget
from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QKeySequence, QPainter

from . import logo

from .. import winapi

ICON = 46     # logical px, square
MARGIN = 14   # gap from the screen corner
MENU_GAP = 8  # gap between the icon and the opened menu

_MENU_STYLE = """
    QMenu {
        background: rgba(18, 20, 28, 235);
        border: 1px solid rgba(255, 255, 255, 30);
        border-radius: 10px;
        padding: 6px;
    }
    QMenu::item {
        color: #eaeaf0;
        font-size: 12px;
        padding: 7px 28px 7px 14px;
        border-radius: 6px;
    }
    QMenu::item:selected { background: rgba(255, 255, 255, 40); }
    QMenu::item:disabled { color: #6d7080; }
    QMenu::separator {
        height: 1px;
        background: rgba(255, 255, 255, 26);
        margin: 5px 8px;
    }
"""


class Launcher(QWidget):
    def __init__(self, on_pick, on_region, on_refresh, on_exit,
                 on_set_video=None, on_settings=None, on_deselect=None):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(ICON, ICON)
        self.setCursor(Qt.PointingHandCursor)

        self._hover = False
        self._tracking = False
        self._detector_ok = None
        self._yt = None      # caption source: None/"loading"/"ready"/"error"
        self._status = ""

        self._menu = QMenu(self)
        self._menu.setWindowFlags(self._menu.windowFlags()
                                  | Qt.FramelessWindowHint
                                  | Qt.NoDropShadowWindowHint)
        self._menu.setAttribute(Qt.WA_TranslucentBackground)
        self._menu.setStyleSheet(_MENU_STYLE)
        self._menu.addAction("Pick window", on_pick)
        self._act_select = self._menu.addAction("Select area", on_region)
        self._act_select.setEnabled(False)  # until a window is tracked
        self._act_refresh = self._menu.addAction("Refresh words", on_refresh)
        self._act_refresh.setEnabled(False)  # nothing to rescan until tracking
        self._act_refresh.setShortcut(QKeySequence("Ctrl+Alt+Shift+R"))
        self._act_refresh.setShortcutVisibleInContextMenu(True)
        # Stop tracking WITHOUT exiting: back to the idle 'pick a window'
        # state — capture stops, the overlay hides, the launcher stays.
        self._act_deselect = None
        if on_deselect is not None:
            self._act_deselect = self._menu.addAction("Deselect window",
                                                      on_deselect)
            self._act_deselect.setEnabled(False)  # nothing tracked yet
        # Point the app at the YouTube video being watched: copy its URL, then
        # click this. Gives cards exact caption timing + audio. (The browser
        # bridge will set this automatically in a later stage.)
        if on_set_video is not None:
            self._menu.addSeparator()
            self._menu.addAction("Use video from clipboard", on_set_video)
        self._menu.addSeparator()
        if on_settings is not None:
            self._menu.addAction("Settings...", on_settings)
        exit_act = self._menu.addAction("Exit", on_exit)
        # Display-only here: the hotkey itself is polled globally by the
        # overlay's tick (this window never has focus to fire a QShortcut).
        exit_act.setShortcut(QKeySequence("Ctrl+Alt+Shift+X"))
        exit_act.setShortcutVisibleInContextMenu(True)

        corner = QApplication.primaryScreen().availableGeometry()
        self.move(corner.left() + MARGIN, corner.bottom() - MARGIN - ICON)

        # Keep both our windows out of captured frames, like the overlay.
        # (winId() also forces the menu's native window into existence.)
        winapi.exclude_from_capture(int(self.winId()))
        winapi.exclude_from_capture(int(self._menu.winId()))

    def roots(self):
        """Our top-level hwnds — what the overlay counts as 'ours' when it
        decides whether the tracked window lost the foreground."""
        return (int(self.winId()), int(self._menu.winId()))

    # ------------------------------------------------------------- state
    def set_status(self, text):
        self._status = text
        self.setToolTip(text)

    def status_text(self):
        return self._status

    def set_state(self, tracking, detector_ok, yt=None):
        if (tracking, detector_ok, yt) == (self._tracking, self._detector_ok,
                                           self._yt):
            return
        self._tracking = tracking
        self._detector_ok = detector_ok
        self._yt = yt
        self._act_select.setEnabled(tracking)
        self._act_refresh.setEnabled(tracking)
        if self._act_deselect is not None:
            self._act_deselect.setEnabled(tracking)
        self.update()

    # -------------------------------------------------------------- menu
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._toggle_menu()
            event.accept()

    def _toggle_menu(self):
        if self._menu.isVisible():
            self._menu.close()
            return
        top_left = self.mapToGlobal(QPoint(0, 0))
        h = self._menu.sizeHint().height()
        self._menu.popup(QPoint(top_left.x(), top_left.y() - h - MENU_GAP))

    # ------------------------------------------------------------- paint
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect().adjusted(1, 1, -1, -1)
        self._draw_logo(p, r)
        if self._detector_ok is False:
            dot = QColor(226, 76, 76)     # detection offline
        elif self._tracking:
            dot = QColor(92, 200, 132)    # tracking a window
        else:
            dot = QColor(150, 154, 170)   # idle, nothing tracked
        self._draw_dot(p, QPoint(r.right() - 8, r.bottom() - 8), dot)
        # Bottom-LEFT dot: the YouTube caption source. Green = caption track
        # ready (cards get exact audio), amber = fetching, red = this video
        # has no usable track. No dot until a video is known.
        yt = {"ready": QColor(92, 200, 132),
              "loading": QColor(226, 179, 76),
              "error": QColor(226, 76, 76)}.get(self._yt)
        if yt is not None:
            self._draw_dot(p, QPoint(r.left() + 8, r.bottom() - 8), yt)

    def _draw_logo(self, p, r):
        """The tile from ui/logo.py, dimmed slightly until hovered."""
        logo.paint_tile(p, QRectF(r), 235 if self._hover else 200)

    def _draw_dot(self, p, center, color):
        """A state dot on a dark disc — red dots are invisible straight on
        the red tile."""
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(18, 20, 28, 220))
        p.drawEllipse(center, 5, 5)
        p.setBrush(color)
        p.drawEllipse(center, 3, 3)

    def enterEvent(self, event):
        self._hover = True
        self.update()

    def leaveEvent(self, event):
        self._hover = False
        self.update()
