"""Run the whole test suite: `python tests/run_all.py` from the project root
(inside the venv).

Unit tests come first (instant, no windows). The live tests each open small
always-on-top windows, load the neural model (a few seconds, up to ~20 s on
a busy machine) and drive the real pipeline against real on-screen pixels —
HANDS OFF the mouse and keyboard while they run. The two simulator tests
draw cyan outlines around whatever detection accepts, so you can watch it
work in real time.

`bench_*.py` files are benchmarks, not tests — run them individually when
tuning detector speed."""

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

UNIT = [
    "test_diff.py",          # frame diff: change masks + settle debounce
    "test_merge.py",         # detector fragments -> one box per text line
    "test_classifier.py",    # caption/not-caption geometry + text rules
    "test_tracking.py",      # ledger: live/seen, fingerprints, clear debounce
    "test_watcher.py",       # instant caption-vanished detection
    "test_translate.py",     # popup word cleanup (pure, no network)
    "test_dictionary.py",    # Wiktionary defs: format, ordering, fallback
    "test_settings.py",      # settings load/save + translate target switch
    "test_audio.py",         # loopback ring buffer: clip math, device rebind
    "test_flashcard.py",     # draft folders: text, provenance, media paths
    "test_youtube_source.py",  # VTT parse + OCR->caption alignment (fixtures)
    "test_bridge.py",        # localhost browser bridge: POST/GET + play_time
    "test_ocr_read.py",      # rec reads Japanese + English (loads the model)
    "test_ocr_arabic.py",    # video language -> rec model: Arabic pack works
]
LIVE = [
    "test_overlay_popout.py",    # popout tracking, deselect-on-close
    "test_overlay_resize.py",    # region edge-resizing, bar auto-hide
    "test_captions_live.py",     # overlay draws/clears a real caption box
    "test_area_rescan.py",       # user-drawn area judges pre-existing caption
    "test_browser_sim.py",       # whole-browser layout + churning chat
    "test_realistic_video.py",   # 5 caption styles over a moving scene
]

results = []
for name in UNIT + LIVE:
    print("\n" + "=" * 66)
    print("RUNNING", name)
    print("=" * 66)
    t0 = time.perf_counter()
    proc = subprocess.run([sys.executable, os.path.join(HERE, name)])
    results.append((name, proc.returncode, time.perf_counter() - t0))

print("\n" + "=" * 66)
all_ok = True
for name, code, took in results:
    ok = code == 0
    all_ok &= ok
    print("%-28s %-14s %6.1fs" % (name, "PASS" if ok else "FAIL (exit %d)" % code, took))
print("=" * 66)
print("SUITE:", "ALL PASS" if all_ok else "FAILURES — see above")
sys.exit(0 if all_ok else 1)
