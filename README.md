# Cappa

Transparent screen overlay that detects burned-in subtitles from any video and turns them into Anki flashcards.

## Run

```bash
pip install -r requirements.txt   # into a venv; also needs the ffmpeg binary on PATH
python run.py                     # or: python -m cappa
```

**Controls:** everything is behind the small icon at the bottom-left of the screen — click it for a menu: *Pick window* (click a window to lock onto it) · *Select area* (drag a box over just the video/subtitle region, then resize it any time by dragging its border) · *Exit*. Hover the icon for status (target · fps · captions). Hovering a detected caption word underlines it; **click it** for its popup (✕ closes it — clicking a word never clicks the video underneath). **Ctrl+Alt+Shift+X** quits from anywhere (also shown next to Exit in the menu). Esc only cancels a pending pick/drag.

## Layout

| Path | Role |
|---|---|
| `run.py` | Launcher |
| `cappa/app.py` | Qt setup + `main()` |
| `cappa/winapi.py` | All Win32/DWM calls (no Qt) |
| `cappa/ui/` | Everything you see: the overlay (paint, pick/select, follow loop) and the corner launcher (icon + menu) |
| `cappa/detection/` | Everything that finds captions, one stage per file — capture → diff → **neural text detection** (PP-OCRv5 via ONNX) → classifier → clear-watching — on a background thread. Map in its `__init__.py` |

## Tests

```bash
python tests/run_all.py       # the whole suite: units first, then live tests
python tests/test_diff.py     # ...or any single file
```

The four unit tests are instant and windowless. The five live tests open
small always-on-top windows, load the neural model (a few seconds each) and
drive the real pipeline against real on-screen pixels — **hands off the mouse
and keyboard while they run**, anything covering their windows changes what
they see. The two simulator tests draw cyan outlines around whatever
detection accepts, so you can watch it work. `tests/bench_*.py` are detector
speed benchmarks, run individually.

See [PLAN.md](PLAN.md) for the full architecture and build order.
