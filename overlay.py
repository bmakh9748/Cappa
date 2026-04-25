import sys
import win32gui
import win32con
from PySide6.QtWidgets import QApplication, QMainWindow
from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QPainter, QColor, QKeySequence, QShortcut


class OverlayWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)

        screen = QApplication.primaryScreen().geometry()
        self.setGeometry(screen)

        QShortcut(QKeySequence("Escape"), self, self.close)

    def showEvent(self, event):
        super().showEvent(event)
        self._make_click_through()

    def _make_click_through(self):
        hwnd = int(self.winId())
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        win32gui.SetWindowLong(
            hwnd, win32con.GWL_EXSTYLE,
            style | win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT
        )

    def paintEvent(self, event):
        painter = QPainter(self)
        # Semi-transparent test rectangle in the center to confirm rendering
        painter.setBrush(QColor(0, 120, 255, 80))
        painter.setPen(Qt.NoPen)
        w, h = self.width(), self.height()
        rect = QRect(w // 2 - 150, h // 2 - 40, 300, 80)
        painter.drawRoundedRect(rect, 8, 8)

        painter.setPen(QColor(255, 255, 255, 200))
        painter.drawText(rect, Qt.AlignCenter, "Overlay active — press Esc to close")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    overlay = OverlayWindow()
    overlay.show()
    sys.exit(app.exec())
