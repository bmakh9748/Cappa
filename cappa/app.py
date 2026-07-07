"""Application entry point: configure Qt for the overlay and run it."""

import os
import sys

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from . import settings as settings_mod
from . import translate
from . import winapi
from .flashcard.timing import set_clip_bounds
from .ui import logo
from .ui.overlay_window import OverlayWindow
from .ui.startup import StartupWindow

APP_ID = "Cappa.Cappa"  # our taskbar identity (AppUserModelID)


def _install_taskbar_icon():
    """The Windows 11 taskbar takes a group's icon from a Start Menu
    shortcut matching the AppUserModelID (never from the window icon), so
    render the logo to an .ico and keep such a shortcut installed. Purely
    cosmetic — any failure must not touch startup."""
    try:
        icon_dir = os.path.join(os.environ["LOCALAPPDATA"], "Cappa")
        os.makedirs(icon_dir, exist_ok=True)
        ico = os.path.join(icon_dir, "Cappa.ico")
        logo.write_ico(ico)
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        # pythonw so launching from Start/a pin opens no console window.
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        winapi.install_start_menu_shortcut(
            APP_ID, "Cappa",
            target=pyw if os.path.exists(pyw) else sys.executable,
            args='"%s"' % os.path.join(root, "run.py"),
            workdir=root, icon=ico)
    except Exception as exc:
        print("[cappa] taskbar icon install skipped:", exc)


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

    # Our own taskbar identity — must precede every window, or the taskbar
    # groups us under python.exe.
    winapi.set_app_id(APP_ID)

    # PassThrough keeps Qt's logical pixels 1:1 with the ratio we divide window
    # bounds by, so the overlay lands accurately on high-DPI displays.
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setWindowIcon(logo.app_icon())  # startup/settings title bar, alt-tab
    _install_taskbar_icon()

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
        set_clip_bounds(app_settings.min_clip_seconds,
                        app_settings.max_clip_seconds)
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
