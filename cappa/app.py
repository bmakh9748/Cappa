"""Application entry point: configure Qt for the overlay and run it."""

import sys

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from . import settings as settings_mod
from . import translate
from .ui.overlay_window import OverlayWindow
from .ui.startup import StartupWindow


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

    # Show the startup window first: pick the translation target, then start.
    # The overlay (and its launcher icon) is created only once the user
    # confirms; afterwards the same window reopens as a live settings panel
    # from the launcher's Settings item. `state` keeps the references alive.
    app_settings = settings_mod.load()
    translate.set_source_language(app_settings.source_language)
    translate.set_target_language(app_settings.target_language)
    state = {"overlay": None}
    startup = StartupWindow(app_settings)

    def on_confirmed():
        translate.set_source_language(app_settings.source_language)
        translate.set_target_language(app_settings.target_language)
        settings_mod.save(app_settings)
        cap_lang = settings_mod.caption_lang(app_settings.source_language)
        if state["overlay"] is None:
            state["overlay"] = OverlayWindow(on_settings=startup.open_settings,
                                             video_language=cap_lang)
        else:
            state["overlay"].set_video_language(cap_lang)
        startup.hide()

    startup.confirmed.connect(on_confirmed)
    startup.show()
    return app.exec()
