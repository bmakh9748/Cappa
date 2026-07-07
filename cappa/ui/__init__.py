"""Everything the user sees: the transparent overlay and the corner launcher.

The map (one window/widget per file):

    overlay_window.py   the transparent, click-through overlay: paint,
                        pick-window/select-area modes, follow loop, word
                        hotspots, hotkeys, worker + recorder + source wiring
    launcher.py         corner icon + menu (pick/select/clipboard video/
                        settings/exit); status tooltip (target · fps ·
                        captions · yt)
    startup.py          startup window = the settings home (Languages and
                        Flashcards tabs), reopened live from the launcher
    word_popup.py       the box a clicked word opens: word · translation ·
                        Create Anki card (card built off-thread)
    template_dialog.py  advanced card-design editor (front/back HTML + CSS)
    logo.py             the Cappa logo as paint code; window/taskbar icons

Detection results arrive here as Qt signals from `cappa.detection.worker`;
nothing in this package computes anything about captions itself. This is the
ONLY package (plus app.py and detection/worker.py's signal layer) that may
import Qt."""
