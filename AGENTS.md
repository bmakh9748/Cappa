# AGENTS.md — how Cappa stays organised

Cappa is a Windows-only Python/PySide6 app: a transparent overlay that detects
burned-in subtitles on any video window with neural OCR, makes each word
clickable, and turns a click into an Anki flashcard draft (translations,
screenshot, audio clip) under `cards/card_NNNN/`.

This file is the **structural contract**. Any change — human or LLM — must
leave the codebase obeying these rules. When a rule and convenience conflict,
the rule wins; if a rule must genuinely change, change it *here in the same
commit*.

## Read this first, in this order

1. This file.
2. The package maps: every package's `__init__.py` docstring lists its files
   and one line each on what they do (`cappa/detection/`, `cappa/source/`,
   `cappa/flashcard/`, `cappa/ui/`). **They are the authoritative maps.**
3. Only then the modules you intend to change.

`README.md` is the user-facing overview. `PLAN.md` is the append-only build
log — useful history, but verify anything it claims against current code.

## The map

```
run.py / python -m cappa  ->  cappa/app.py main()
cappa/
  app.py         Qt setup; startup window -> overlay + launcher wiring
  winapi.py      ALL raw Win32/DWM/ctypes. Knows Windows, never Qt.
  settings.py    persisted user settings (settings.json). No Qt.
  translate.py   sentence/word translation (free Google endpoint). No Qt.
  dictionary.py  word meanings: Wiktionary first, Google hint/fallback. No Qt.
  audio.py       WASAPI loopback ring buffer (LoopbackRecorder). No Qt.
  ui/            everything visible (the ONLY Qt package besides app.py
                 and detection/worker.py's signal layer)
  detection/     finds captions on screen; one pipeline stage per file,
                 chained by worker.py on a background thread
  source/        YouTube caption track = the timing/audio oracle; browser
                 bridge for "which video, what position"
  flashcard/     a clicked word -> a draft folder; builder.py assembles,
                 clip.py picks + cuts the audio window
extension/       browser extension feeding source/bridge.py
tests/           script-style tests; run_all.py is the suite
cards/           saved drafts (gitignored) — ALSO the project's bug tracker
```

## Structural rules

1. **One domain per package, one job per file.** A file that starts doing two
   jobs gets split along the data-flow seam (that is how `detection/` got one
   stage per file and how `flashcard/clip.py` split from `builder.py`). If a
   module passes ~500 lines, look for the seam.
2. **Every package `__init__.py` docstring holds its map.** Adding, renaming,
   or splitting a file means updating that map in the same change. If the
   README layout table is affected, update it too.
3. **Qt stays in `ui/` + `app.py`**, plus the signal layer of
   `detection/worker.py`. Detection stages, `source/`, `flashcard/`,
   `translate/dictionary/audio/settings/winapi` must import no Qt — that is
   what makes them unit-testable in isolation.
4. **`winapi.py` owns Win32.** No raw `ctypes`/`win32gui` calls anywhere else;
   `winapi.py` itself never imports Qt.
5. **The UI thread does no heavy work.** OCR, translation, card building,
   network, ffmpeg — all on worker/daemon threads. Results cross to the UI
   only via Qt queued signals. The UI consumes `CaptureWorker`'s signals; UI
   and pipeline never reach into each other's internals.
6. **One clock.** Cross-module timestamps are `time.monotonic()` — caption
   appear/clear stamps, the audio ring buffer, the bridge's position history
   all share it. Never mix in wall-clock time.
7. **Fail soft, and say so.** Optional capabilities (audio device, network,
   yt-dlp, models, the bridge) degrade to a status string or a draft note —
   never a crash, and never a silent degradation. A card records every missing
   piece in `notes`; a user-disabled piece is skipped *without* a note.
8. **No LLM anywhere in the translation/dictionary path. Hard user rule.**
   Free, key-less endpoints only (Wiktionary, deep-translator's Google).
   Never add a Claude/OpenAI/paid-API call there, even as a fallback.
9. **Constants carry their reason.** Tunable numbers live at the top of their
   module with a comment saying *why that value* — usually citing the card
   that motivated it (`card_0061`, `card_0075`, …). A magic number without a
   why-comment is a defect.
10. **`cards/` is the bug tracker.** Every card bug fix cites its card number
    in the comment and gets a regression test citing the same number. Each
    card's `metadata.json` is a provenance record precise enough to debug
    from — keep it that way: **add keys, never rename or repurpose them**
    (a future `.apkg` exporter and old cards both read them).
11. **New setting = four touches:** a field in `settings.py`, a row in
    `ui/startup.py`, the push in `app.py`'s `apply_settings()`, and module
    state in the consuming module (read at call time, so Save applies live).

## Tests

- Tests are plain scripts with `assert` (not pytest). Run one file directly,
  or the suite: `python tests/run_all.py` (venv, repo root).
- A new test file must be registered in `run_all.py`, in `UNIT` (instant,
  windowless, no network) or `LIVE` (opens windows, loads models, needs the
  mouse/keyboard untouched). Agents: run the relevant `UNIT` files after any
  change; run `LIVE` only when the user is present and expecting it.
- Every behavioural fix ships with a regression test in the matching
  `tests/test_*.py`, added to that file's `__main__` list.
- Unit tests must stay network-free: fake the translator/source (see
  `FakeSource` in `test_youtube_source.py`), use `tests/fixtures/`.

## Git

- Commit style: `type(area): plain-language sentence` — see `git log` for the
  voice (e.g. `fix(source): caption track picked by language first, not by
  being manual`). One concern per commit.
- **Never add Co-Authored-By, "Generated with", or any AI self-credit to
  commits or PRs.** Hard user rule.
- Never commit `cards/`, `screenshots/`, media, `settings.json`, venvs,
  caches (see `.gitignore`). Update `requirements.txt` when dependencies
  change.
- Don't commit unless the user asks; when they do, keep unrelated worktree
  changes out of the commit.

## Verification bar

Before declaring a change done: the affected `UNIT` test files pass, any new
behaviour has a test, the package maps/README still tell the truth, and no
rule above is broken. If you changed `detection/` or `ui/` behaviour that
tests can't cover, say so explicitly instead of implying it was verified.
