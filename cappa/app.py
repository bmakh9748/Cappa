"""Application entry point: configure Qt for the overlay and run it."""

import sys

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from .ui.overlay_window import OverlayWindow


def main():
    # Captions can be in any script; make the console print them as-is.
    # (Redirected/piped stdout on Windows defaults to the ANSI codepage,
    # which can't encode CJK — 'replace' keeps even that from ever crashing.)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    # PassThrough keeps Qt's logical pixels 1:1 with the ratio we divide window
    # bounds by, so the overlay lands accurately on high-DPI displays.
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    # Starts idle: only the launcher icon is visible; the overlay window
    # shows itself when a window is picked. The reference keeps it alive.
    overlay = OverlayWindow()  # noqa: F841
    return app.exec()
