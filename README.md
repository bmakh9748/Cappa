# Cappa

Transparent screen overlay that detects burned-in subtitles from any video and turns them into Anki flashcards.

## Run

```bash
pip install -r requirements.txt   # into a venv; also needs the ffmpeg binary on PATH
python run.py                     # or: python -m cappa
```

On launch a small startup window shows the settings in two tabs — *Languages* (video language, translation language) and *Flashcards* (what each card collects — word/sentence translations, screenshot, audio and its clip length — and whether it goes on the card's front or back, or off entirely); set them and click *Start Cappa*.

**Controls:** everything is behind the small icon at the bottom-left of the screen — click it for a menu: *Pick window* (click a window to lock onto it) · *Select area* (drag a box over just the video/subtitle region, then resize it any time by dragging its border) · *Use video from clipboard* (paste a YouTube URL for exact caption timing/audio — or install `extension/` so it's automatic) · *Settings…* (reopens the startup window) · *Exit*. Hover the icon for status (target · fps · captions · yt). Hovering a detected caption word underlines it; **click it** for its popup (✕ closes it — clicking a word never clicks the video underneath). **Ctrl+Alt+Shift+X** quits from anywhere (also shown next to Exit in the menu). Esc only cancels a pending pick/drag.

## Layout

| Path | Role |
|---|---|
| `run.py` | Launcher |
| `cappa/app.py` | Qt setup + `main()`: startup window → overlay + launcher |
| `cappa/winapi.py` | All Win32/DWM calls (no Qt) |
| `cappa/settings.py` | Persisted user settings (`settings.json`) |
| `cappa/translate.py` | Sentence/word translation — deep-translator's free Google endpoint, **never an LLM** |
| `cappa/dictionary.py` | Word meanings — Wiktionary definitions, Google as hint + fallback |
| `cappa/audio.py` | WASAPI loopback ring buffer (record what you hear, clip retroactively) |
| `cappa/ui/` | Everything you see: the overlay, corner launcher, settings window, word popup |
| `cappa/detection/` | Everything that finds captions, one stage per file — capture → diff → **neural text detection** (PP-OCRv5 via ONNX) → OCR → classifier → clear-watching — on a background thread |
| `cappa/source/` | YouTube caption track as the timing oracle: VTT parsing, OCR-line alignment, yt-dlp/ffmpeg, browser bridge |
| `cappa/flashcard/` | A clicked word → a card draft folder under `cards/card_NNNN/` (audio window choice, screenshot, provenance) |
| `extension/` | "Cappa Bridge" browser extension: which video + position → the bridge |

Every package's `__init__.py` docstring holds its per-file map — read those first. [AGENTS.md](AGENTS.md) is the structural rulebook for anyone (human or LLM) changing the code.

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
