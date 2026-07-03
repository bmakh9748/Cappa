# Video Flashcard Tool — Project Plan

Paste this file at the start of every new Claude session to restore full context.

---

## What This Is

A Windows desktop app for learning vocabulary from foreign language videos. The user watches videos normally in their browser (YouTube or any site). The app runs as a transparent overlay on top of the browser window, detects subtitle text anywhere on screen in real time, and makes each word clickable. Clicking a word shows a popup with the definition, and the user can save it as a flashcard.

**Key constraint:** Subtitles are burned into the video and can appear anywhere on screen — not just the bottom. Some videos place subtitles on the speaker's head, in the middle, wherever the editor put them. The app must handle this dynamically.

---

## How It Works (Core Loop)

### The detection pipeline — designed to be lightweight on any PC

**Step 1 — Screen capture (~5ms)**
Capture the browser window at ~30fps using `mss` (fast pixel capture, no encoding). This is essentially free CPU-wise.

**Step 2 — Frame diff (~5ms)**
Compare current frame to previous frame using numpy array subtraction. If nothing meaningful changed, do nothing. OCR is never triggered on a static frame. This means OCR fires maybe 20-30 times per minute (when subtitles change) instead of 1800 times per minute.

**Step 3 — Change region analysis (~5ms)**
When pixels do change, look at WHERE they changed. A subtitle appearing creates a cluster of pixel changes in a coherent region. Scattered changes across the frame = background motion, ignore. Clustered text-shaped changes = potential subtitle, proceed.

**Step 4 — OCR on changed region only (~100-200ms, only when needed)**
Crop just the changed region and run PaddleOCR on it. PaddleOCR is significantly faster than Tesseract on real-time tasks and returns all text blocks with bounding boxes in one call. Only fires when a subtitle actually appears or changes.

**Step 5 — Subtitle classifier (microseconds)**
Rule-based scorer looks at each text block returned by OCR and decides if it's subtitle text or UI/graphic text. Scores based on:
- Is it a short phrase (not a logo or long body text)?
- Is it high contrast against its local background?
- Did it appear suddenly (change detection already told us this)?
- Is it not in a corner or edge of the screen?
- Is its font size consistent with previous detected subtitles in this session?

Highest scoring block(s) = subtitle. No ML model needed, no API calls, runs in microseconds.

**Step 6 — Overlay update**
Place transparent clickable hotspots over each word in the detected subtitle. The overlay is click-through everywhere except these hotspots, so the browser and YouTube controls work normally.

---

## On Word Click

1. Freeze the hotspots (don't update overlay while popup is open)
2. Capture current frame as PNG screenshot
3. Extract ±2s audio clip around current timestamp using ffmpeg → save as MP3
4. Send (clicked word + full sentence + source language + target language) to Claude API
5. Claude returns: translation, romanisation if needed, part of speech, example sentence
6. Small popup appears near the clicked word with all of the above
7. User sees known/unknown toggle + "Save Flashcard" button
8. If saved → card added to session queue silently in background

---

## Flashcard Export

- User opens the control panel and clicks Export
- `genanki` bundles all saved cards + media (screenshots + audio clips) into a single `.apkg` file
- User drags `.apkg` into Anki — all media imports automatically
- Each card contains: word, translation, sentence, screenshot, audio clip

---

## UI Structure

### The overlay (covers the browser window, always on top)
- Fully transparent and click-through by default
- Only interactive at word hotspot regions
- Word popup appears near the clicked word, not in a fixed position

### The control panel (small separate window, lives in corner)
- Language picker: source language + target language
- Session stats: words saved today, cards in queue
- Export button
- Settings (subtitle detection sensitivity, hotspot appearance etc.)

### First screen (when app launches)
- Source language selector
- Target language selector  
- "Start watching" button — user then clicks on their browser window to select it
- App locks onto that window and begins the detection loop

---

## Tech Stack

| Layer | Tool | Reason |
|---|---|---|
| UI framework | **PySide6** | LGPL licensed, actively maintained, full CSS-like styling |
| UI theme | **qt-material** | Material Design dark theme, 2-line setup |
| Screen capture | **mss** | Fastest Python screen capture library, low CPU |
| Frame diffing | **numpy** | Array subtraction for pixel change detection, ~5ms |
| Text detection | **PP-OCRv5 mobile** via `rapidocr` + `onnxruntime` | Same weights PaddleOCR uses, ~6x faster than Paddle's runtime on Windows CPU |
| OCR (reading text) | **PP-OCRv6 small (multi-script)** via `rapidocr` + `onnxruntime` | One model reads Japanese/Chinese/English/…, ~20 ms/line; paddlepaddle no longer needed at all |
| Overlay | **pywin32 / win32gui** | Transparent always-on-top window on Windows |
| Audio extraction | **ffmpeg** via `ffmpeg-python` | Slice audio clips by timestamp |
| Translation | **Claude API** (`claude-sonnet-4-20250514`) | Translation + definition on word click only (not real-time) |
| Flashcard export | **genanki** | Generates Anki `.apkg` files with embedded media |
| OS | **Windows only** (for now) | Overlay behaviour uses Win32 APIs |
| Language | **Python 3.11+** | |

### Install commands
```bash
pip install PySide6 qt-material mss numpy rapidocr onnxruntime pywin32 ffmpeg-python genanki anthropic
```

Also requires:
- **ffmpeg binary** on PATH: https://ffmpeg.org/download.html
- PaddleOCR downloads its own models on first run (~8MB, automatic)

---

## Key Architecture Decisions

- **No video importing** — app watches the browser window directly. Videos never enter the app.
- **OCR fires on change, not on a timer** — frame diff means OCR only runs when something on screen actually changes. Lightweight on any PC.
- **PaddleOCR over Tesseract** — Tesseract is too slow (300-800ms on full screen). PaddleOCR is built for real-time and handles the cropped change region fast.
- **Rule-based subtitle classifier, not ML** — subtitles have consistent enough traits that a scorer with 5 rules outperforms anything that needs training, and runs in microseconds.
- **Claude API only on word click** — not in the detection loop. The expensive call only happens when the user deliberately clicks a word. Could be 10 times per video session, not thousands.
- **Subtitles can be anywhere on screen** — the change-detection approach doesn't assume a fixed subtitle region. It finds text wherever it appears on each frame change.
- **PySide6 over PyQt6** — identical API, better license for future distribution.
- **qt-material over QFluentWidgets** — QFluentWidgets looks better but adds complexity too early.

---

## Build Order

1. **Overlay window** — transparent, always-on-top PySide6 window that covers the browser
2. **Screen capture loop** — mss capturing the locked browser window at 30fps
3. **Frame diff engine** — numpy change detection, only flag meaningful pixel clusters
4. **PaddleOCR integration** — OCR on changed regions, return word bounding boxes
5. **Subtitle classifier** — rule-based scorer to filter subtitle text from UI text
6. **Word hotspots** — clickable transparent regions on the overlay mapped to word positions
7. **Word click popup** — Claude API call on click, show definition/translation near word
8. **Control panel UI** — language picker, session stats, export button
9. **Audio extraction** — ffmpeg clip on word click
10. **Screenshot capture** — frame capture on word click
11. **Flashcard export** — genanki .apkg generation

---

## Project Layout

```
run.py                  # launcher — `python run.py` (or `python -m cappa`)
cappa/
  __init__.py           # version / package marker
  __main__.py           # `python -m cappa`
  app.py                # Qt setup + main(): builds and runs the app
  winapi.py             # ALL Win32/ctypes/DWM — no Qt (input, windows, click-through)
  ui/                   # everything the user SEES
    overlay_window.py   # OverlayWindow: paint, pick/select modes, follow loop, worker wiring
    launcher.py         # corner icon + pop-up menu (all controls live here)
  detection/            # everything that FINDS captions (see its __init__ for the map)
    capture.py          # screen grab (mss -> numpy BGRA)     every frame  ~10ms   [done]
    diff.py             # what changed since last frame       every frame  <1ms    [done]
    stability.py        # watch live captions for vanishing   every frame  <1ms    [done]
    detector.py         # NEURAL text detection (PP-OCRv5 ONNX) on change  ~60ms   [done]
    tracking.py         # ledger: live captions, judged junk                       [done]
    classifier.py       # caption or not-caption (geometry + text rules)           [done]
    worker.py           # background QThread chaining the stages, emits results
    ocr.py              # read text in accepted boxes (PP-OCRv6 ONNX)  ~20ms       [done]
tests/
  run_all.py            # the whole suite; units first, then live tests
  test_diff/classifier/tracking/watcher.py     # unit tests, instant, windowless
  test_overlay_*.py, test_captions_live.py     # live overlay behaviour
  test_browser_sim.py, test_realistic_video.py # end-to-end sims (draw what they detect)
  bench_*.py            # detector speed benchmarks (run individually)
```

Boundary rules:
- `winapi.py` knows Windows but not Qt; the Qt modules call `winapi.*` and never touch raw Win32.
- `detection/` stages are plain, testable units with no Qt. Only `worker.py` touches Qt (its
  signals) — it chains the stages on a background thread and emits results. The overlay hands
  it a `capture_region()` callback and consumes its signals; the two never reach into each other.
- Threading: the loop runs off the UI thread because the neural scan costs ~150 ms. Cross-thread
  results use Qt **queued signals**, which Qt delivers on the main thread automatically.

## Current Status

> **Step 1 (Overlay window) — done**, and refactored from one 489-line file into the
> `cappa/` package above. It's a transparent, always-on-top, click-through PySide6
> window. It can lock onto a chosen window (Pick window → click the target) and follow
> it as it moves/resizes, using DWM extended frame bounds so it lands on the real edges.
> **Select area** drags a box over just the video/subtitle region, stored as fractions
> of the window so it follows moves and resizes. A locked region can then be resized like
> a window: grip handles mark its border, and dragging any edge/corner (EDGE_GRIP band)
> adjusts it, clamped to the tracked window and a minimum size.
> Click-through is toggled per-region via
> cursor hit-testing (`_interactive_rects`) — the groundwork word hotspots (#6) reuse.
> Controls live in the corner launcher (see the launcher paragraph below; it replaced an
> in-overlay control bar). The overlay parks off-screen when the tracked window isn't in front
> or is minimized (so it never floats over other apps and always comes back). Always-on-top
> targets (a browser's picture-in-picture popout) never park for focus loss — they're
> visible without owning the foreground — and the overlay re-raises itself above them
> inside the on-top band. Closing the tracked window (destroyed **or** hidden — some apps
> hide their popout on ✕) deselects back to idle with a "Window closed" status.
> **Ctrl+Alt+Shift+X** quits from anywhere; **Esc** only cancels a pending pick/drag (left free
> for YouTube). Env fully installed in `.venv`.
>
> **Step 2 (Screen capture) — done.** `pipeline/capture.py` + `pipeline/worker.py`: a
> background `QThread` grabs the tracked window/region at ~30 fps via mss and emits frames +
> a measured fps (shown in the control bar as "· 30 fps" while tracking). The overlay exposes
> `capture_region()` (physical-pixel rect, or None when parked / picking / no target) which the
> worker polls each frame; the thread is torn down cleanly on quit. Verified at ~30 fps with
> frames crossing to the UI thread and a clean shutdown. The overlay window is excluded from
> capture (`WDA_EXCLUDEFROMCAPTURE`), so its own border/bar never appear in grabbed frames —
> the diff can't see our repaints and OCR will never read our own UI.
>
> **Step 3 (Frame diff) — done.** `pipeline/diff.py`: per-pixel abs-diff on a downscaled
> (every 4th px) copy of consecutive frames, **per-channel max** rather than grey mean (a
> grey diff is blind to equal-brightness colour changes, e.g. red→blue). A fraction-changed
> gate plus a settle debounce means `feed()` fires exactly once per change burst — on the
> settled frame OCR wants — never during a fade-in and never on a static region. The worker
> chains capture → diff and emits `settled(frame)`; the bar shows a running trigger count
> ("· N triggers") so the fires-per-minute promise is observable while tuning. Knobs
> (downscale, pixel threshold, fraction gate, settle frames) live at the top of diff.py.
> Verified with synthetic fade-in/clear/resize sequences and live end-to-end: one on-screen
> change while tracking = exactly one trigger.
>
> **Step 4 build-order (Region analysis) — done.** `pipeline/regions.py`. Key design shift
> from the original sketch: a *playing* video never goes globally quiet, so "cluster the
> changed pixels" alone can't find captions. What separates a burned-in caption is per-pixel
> **temporal** behaviour — caption pixels change once (the pop-in) then hold perfectly still
> while video pixels keep churning, and letterboxing/static UI never changes at all.
> `RegionTracker` keeps a per-pixel quiet-streak counter over `diff.mask` (diff.py now
> exposes `mask` + `sample` after each feed). Candidates = pixels that just completed a
> STABLE_FRAMES streak ∧ have changed before ∧ sit on a strong luminance edge (text is
> strokes; flat colour going static contributes nothing — that's also what stops a paused
> flat scene or a colour flip from becoming a "caption"). Row-projection turns candidates
> into horizontal bands; a height cap rejects a detailed scene going static; when a box's
> stroke pixels start moving again → "cleared". The worker emits `regions((events, boxes))`;
> the overlay draws live cyan outlines on the boxes (the seed of Step 6's hotspots) and the
> bar shows "· N caption boxes". Verified synthetically (caption pops in over churning noise
> → one pixel-exact appeared event; clears once; paused scene + flat pop-up rejected) and
> live end-to-end (real window, flipping background, real rendered text: boxed within ~0.3 s,
> cleared on hide). Whole diff+regions chain: **~0.3 ms/frame**.
>
> **Step 5 (Classifier), geometric half — done.** `pipeline/classifier.py`, chained after
> the tracker in the worker: the tracker's job is recall (box every stable text-shaped
> thing), the classifier's is precision (keep only caption-behaved boxes). Rules, all
> pre-OCR: size band (≥16 px, ≤14% of a roomy region), wide-short aspect (≥2.5),
> horizontally centred (±12% of width) OR matching an already-accepted caption's height and
> vertical zone (history, 8 entries — how non-centred styles earn trust), and a burst rule:
> >3 simultaneous appearances = scroll/redraw, reject the batch and distrust everything for
> 0.7 s. Cleared events only surface for accepted boxes. Text-content rules (short phrase?
> real words?) join when OCR lands. Latency tuned down: STABLE_FRAMES 6 (~215 ms measured
> appear latency on the simulated-video suite, all 5 styles still detected, zero false
> positives). MIN_EVIDENCE in regions.py is now expressed in full-res px² so retuning
> DOWNSCALE doesn't silently change sensitivity.
>
> **Detection pivot — hybrid neural detection (done).** Real-YouTube testing of the pure
> stability detector measured ~70% recall / ~40% precision: real videos are often mostly
> static (everything stabilises, hence junk boxes) and real captions aren't pixel-stable
> (compression shimmer, semi-transparent CC backgrounds — hence misses). Replaced "find the
> captions by stability" with "find text with a pretrained neural detector, decide WHEN to
> look with the cheap stages": diff notices change → throttled `detector.py` scan (PaddleOCR
> DBNet, PP-OCRv5 mobile, run at ≤736px long side — measured: 480px finds NOTHING on a full
> 1920×1080 capture, 736px finds even ~26px captions there at ~330 ms; smaller captures like
> popouts or a selected area shrink less and scan faster) boxes all text →
> `tracking.py` ledger keeps persistent junk from being re-judged every scan → geometric
> classifier keeps caption-behaved boxes → `stability.py` watches accepted boxes' stroke
> pixels to emit "cleared" within a frame or two (cumulative stroke-loss tally, shimmer-
> tolerant), with a ledger sweep (3 scans not seeing a live box) as the safety net. Also in
> this pass: the package was reorganised into `ui/` and `detection/` (one stage per file, map
> in `detection/__init__.py`). Measured on the simulated-video suite through the real worker
> thread: **5/5 caption styles detected (incl. no-outline and 16px), zero false positives
> (strict), appear latency 190–830 ms** (scan throttle SCAN_INTERVAL 0.5 s; a cleared line
> triggers a fast rescan for the next line). Known workarounds: `enable_mkldnn=False`
> (PaddlePaddle 3.3 oneDNN crash on Windows — a fix would cut scan time 3-5×; ONNX export is
> the other speedup path) and `PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK` (connectivity probe can
> hang; model is cached in `~/.paddlex` after first run). **Both retired by the ONNX runtime
> swap below.**
>
> **Stream-overlay fixes (real-screenshot driven).** Two user screenshots (VTuber stream
> clips: live-chat overlay burned into the frame, superchat banners, chat churning every
> second) exposed real failure modes, all fixed and covered by tests: (1) the burst rule
> fired on the raw new-box count, so churning chat perma-rejected everything including the
> caption — it now applies AFTER individual judging, only when >3 boxes PASS the caption
> gates at once (a real scroll); chat dies per-box on the off-centre rule and can never drag
> the caption down. (2) CENTER_TOLERANCE 0.12→0.22: in default YouTube layout the video pane
> (and its centred captions) sits ~16% left of the window centre. (3) A **baseline scan**
> right after lock-on memorises text already on the page without judging it, so centred page
> furniture (e.g. the search bar) can't be grandfathered in as a caption — the cost is that
> a caption already on screen at lock-on isn't boxed until the next line (whole-window locks
> only; user-drawn areas skip the baseline, see the select-area rescan note below). (3b) **Content
> fingerprints in the ledger** — the "it NEVER works on the real video" bug: seen-memory
> matched by overlap alone, and consecutive caption lines land in the same spot with
> similar-shaped boxes, so the line memorised at lock-on silently muted every line after it
> (each new line refreshing the memory!). Seen entries now carry a coarse per-box pixel
> fingerprint (2×4 block-mean grey on the diff grid); suppression requires location AND
> content to match, so the same spot showing NEW text is judged fresh while a static
> watermark stays muted. Also MIN_HEIGHT_PX 16→12 so captions inside small popout windows
> survive the size gate. (3c) The classifier's "matches accepted history" rule was REMOVED:
> a hardened test caught chat lines of caption-ish height sneaking in through it once a real
> caption seeded it — and since only centred boxes can seed it, it could never admit the
> off-centre styles it was meant for. Off-centre subtitle styles = Select area today, OCR
> text-trust later. The suite lives in `tests/` (`python tests/run_all.py`); the two sims
> draw cyan outlines around accepted boxes so detection is visible while they run. (3d)
> **Value-based clear watching** — the "fullscreen works much worse" report: windowed video
> is downscaled by the browser (smooths compression noise), fullscreen is 1:1 where H.264
> shimmer wobbles caption-edge pixels every frame; the watcher's cumulative changed-pixel
> tally crept past CLEAR_LOST and cleared live captions moments after boxing them. The
> watcher now remembers each stroke pixel's VALUE at watch time and counts it lost only
> while it currently differs (>40 grey): shimmer self-recovers, real clears stay lost
> whether video moves in behind or a static background settles. Shimmer legs added to the
> watcher unit test and the realistic sim. Remaining fullscreen caveat: YouTube's control-
> bar gradient darkening the caption zone on mouse-move can still blip a clear+re-accept;
> and if fullscreen ever shows `0 text` in every `[cappa]` scan line, that's a screen-
> capture (MPO) issue — different fix. (4) Console
> diagnostics: every scan prints `[cappa] scan: N text | M new | K accepted | L live` plus
> per-box rejection reasons, and `[cappa] detector ready/FAILED` after the model load (which
> can take 2–20 s depending on machine load). Known remaining limit until OCR text rules:
> centred in-video chat bubbles (screenshot 2) pass the geometric gates; for such busy
> streams, Select area on the video/caption zone is the reliable mode.
>
> **ONNX runtime swap (done) — detection ~6x faster, same model.** The Paddle oneDNN crash
> is still unfixed as of paddlepaddle 3.3.1 (latest; retested — same PIR/oneDNN error, and
> `FLAGS_enable_pir_api=0` doesn't help), so instead of waiting, `detector.py` now runs the
> SAME PP-OCRv5 mobile det weights through **onnxruntime** via a det-only RapidOCR engine
> (RapidAI ships the pre-converted ~4.6 MB .onnx, downloaded once into the venv on first
> run; converting ourselves is currently impossible on Windows — paddle2onnx 2.x has a DLL
> mismatch and then demands a nightly paddle). Measured on the same synthetic 1080p frame:
> ~375 ms (Paddle, mkldnn off) → **~55-95 ms** end-to-end depending on machine load (~40 ms
> raw forward pass), identical box out. Two traps now encoded in the engine params:
> RapidOCR's default `limit_type='min'` UPSCALES frames (made ONNX benchmark as slow as
> Paddle until caught — ours is `'max'` + we pre-shrink), and its logger WARNs on every
> text-free scan (level raised after construction; raising it before is undone by the
> import). `Det.box_thresh` carries the old MIN_SCORE 0.6 gate. Full suite re-run: 9/9
> PASS, realistic sim 5/5 styles, zero false positives. Cheap scans then paid for a lower
> scan cadence: SCAN_INTERVAL 0.5 s → **0.2 s**, taking sim appear latency from 169-834 ms
> to **197-538 ms** (still 5/5, zero FP). 0.15 s measured *worse* (456-590 ms — scans run
> inline on the worker thread, so too-frequent scans crowd out frame grabs); CPU while video
> plays is ~35-45% of one core at 0.2 s vs ~15% at 0.5 s — worth a settings-panel knob
> later. Next latency levers if ever wanted: Select area (smaller capture = faster scans =
> lower viable cadence, free today) and GPU via onnxruntime-directml (~10 ms forward pass). Knock-on: both Paddle workarounds are gone
> from the runtime path, and once `ocr.py` (recognition) also goes through rapidocr, the
> ~700 MB paddlepaddle+paddleocr install could be dropped entirely (kept in
> requirements.txt for now as the planned rec path).
>
> **UI: corner launcher replaces the control bar (done).** The wide in-overlay bar is
> gone. `ui/launcher.py`: a 46px translucent rounded icon (placeholder "C" glyph until
> the real logo is designed — swap `_draw_glyph()` then) parked bottom-left of the
> primary screen, always on top, Parsec-style. Clicking it pops a dark menu with exactly
> the available actions — Pick window / Select area (disabled until a window is tracked)
> / Full screen / Exit (replaces the old ✕) — and a dot on the icon mirrors state (green
> tracking · grey idle · red detector failed). The status line the old bar displayed
> (target · fps · caption count — every "the bar shows…" mention above) is now the
> icon's hover tooltip, `launcher.status_text()` in tests. It is a top-level window, NOT
> an overlay child: it never follows/parks/clips with the tracked region (the bar-fit
> auto-hide logic is deleted), stays reachable even while the overlay is parked, is
> excluded from capture like the overlay, never takes focus (`WindowDoesNotAcceptFocus`),
> and its hwnds are whitelisted in the overlay's foreground check so opening the menu
> can't park the overlay. It hides during pick/select so it can't be picked or block the
> drag.
>
> **Select-area rescan (done, user-reported).** Selecting an area on a PAUSED video whose
> caption was already on screen never boxed it: the baseline scan memorised the caption as
> page furniture, and with nothing changing on screen no later scan could rescue it. Now a
> USER-DRAWN region — Select area, or any edge-resize of it — skips the memorise pass:
> pointing at the caption zone is an explicit "captions live here", so the first scan
> judges pre-existing text. Whole-window locks (Pick window) keep the baseline, since
> that's exactly where the search-bar false positive lives; a pre-existing caption there
> still waits for the next line. Implementation: `CaptureWorker(user_area_provider=...)`,
> the overlay passes `lambda: self._region is not None` (read off-thread at scan time,
> atomic). Covered by `tests/test_area_rescan.py` — both legs on the real worker thread.
>
> **Idle state replaces "Full screen" mode (done, user call).** The untracked full-screen
> mode never detected anything — capture only runs with a picked target — so its grey
> whole-screen border was pure noise, and the user called it useless. Removed: the app now
> idles as just the launcher icon (overlay hidden; `_go_fullscreen()` → `_go_idle()`), the
> menu is Pick window / Select area / Exit, a closed tracked window deselects to idle, and
> Esc during a pick cancels to idle. The Exit menu item now displays the quit hotkey
> (which always existed — the overlay's tick polls it globally; the menu entry is
> display + a normal in-focus shortcut). `app.py` no longer show()s the overlay at startup.
> The hotkey itself became **Ctrl+Alt+Shift+X** right after: Ctrl+Shift+X collides with
> browser shortcuts (user-reported), and a triple-modifier combo is bound by ~nothing.
>
> **OCR text rules + clear debounce (done).** `detection/ocr.py`: recognition on the
> cropped, accepted boxes through the SAME onnxruntime stack — rapidocr's default
> multi-script rec model (PP-OCRv6 small), which measured BEST on both Japanese and
> English (100% on rendered caption lines, conf ~0.98) *and* fastest (~10-20 ms/line),
> beating the v5-ch and japan-specific packs. No language setting, no regression risk for
> non-Latin captions — a hard requirement (the user's videos are Japanese). Cost
> discipline: rec runs only for boxes that pass the geometric classifier (a few times a
> minute), so the scan path is unchanged. The text rules (`classifier.text_verdict`) are
> **fail-open**: they reject ONLY positively-identified junk read with ≥0.75 confidence —
> letter-ratio < 30% (clocks, "1080p60", counters; `str.isalpha()` counts kana/kanji so
> CJK passes like English) and URL/@handle watermarks. Empty/low-confidence/unreadable →
> keep (geometry already vetted it; scripts the model can't read must keep working, and if
> the rec model fails to load entirely, detection runs exactly as before). Accepted text
> prints ascii()-escaped in the scan diagnostics (cp1252 consoles must not crash the
> worker). ALSO: **clear debounce** — the watcher misreading a brief overlay (YouTube's
> control-bar gradient) as a vanish used to flicker cleared+appeared. `ledger.clear()` now
> parks the box as PENDING (no event); the post-clear fast rescan either `resurrect()`s it
> silently (same spot AND accept-time content fingerprint, re-measured on the pending
> box's own coordinates — comparing via the wobbling scan box breaks the match) or
> `expire_clears()` surfaces the real clear after CLEAR_CONFIRM (0.35 s). Real clears
> land ~0.3 s later; blips produce nothing. Knock-on: **paddlepaddle/paddleocr dropped
> from requirements.txt** — detection AND recognition are both rapidocr+onnxruntime now
> (the old `bench_paddle_*.py` scripts still want paddle; they're historical). New tests:
> `test_ocr_read.py` (real Japanese+English reads through TextReader), text-rule legs in
> `test_classifier.py`, hysteresis legs in `test_tracking.py`. Suite: 11/11.
>
> **Next step:** user validation on real (Japanese) YouTube videos, then word hotspots
> (#6) — rec can return per-word boxes (rapidocr `return_word_box`) when the overlay
> needs clickable words; temporal text identity ("same caption?" across scans) is now
> possible via the ledger + rec text if needed.
>
> _Deferred / known limits:_ settings panel (needs its own planning pass); multi-DPI across
> mixed-scaling monitors may be slightly off (uses primary-screen DPR); tracking a
> **non-maximized** window on Win11 includes its rounded corners, so a few desktop pixels
> behind it bleed into the region — background activity there (cursor blink etc.) adds
> occasional noise triggers (region clustering should reject these; revisit then).

## Detection — the hard part (Steps 2–5)

The plan's promise is "OCR fires ~20–30×/min, not 1800×/min." Everything hinges on the
diff → region → OCR → classify chain being both cheap and accurate. Open problems to solve as
we build each stage:

- **Diff sensitivity (Step 2) — built, tune as real videos land:** per-pixel abs-diff on a
  downscaled frame + "fraction of pixels changed" gate + settle debounce, as planned — but
  per-channel max instead of greyscale (grey is blind to equal-brightness colour changes).
  Current knobs in `diff.py`: downscale 4, pixel threshold 18/255, fraction 0.004, settle 3
  frames. Known noise floor ~0.002 from desktop bleed at rounded window corners; playing
  video will hold the diff "pending" continuously — that's what region clustering is for.
- **Where are the captions (Step 3) — SUPERSEDED by the neural detector:** the pure
  stability approach measured 70%/40% recall/precision on real YouTube and was replaced by
  `detector.py` (pretrained PaddleOCR DBNet) for finding text; per-pixel stability lives on
  in `stability.py` with one job it is genuinely good at — noticing within a frame or two
  that an accepted caption's stroke pixels started moving (i.e. it vanished). "Which text is
  a caption" is the classifier's job.
- **OCR cost/accuracy (Step 4) — both halves in and measured:** detection ~55-95 ms/scan
  at ≤736px (PP-OCRv5 mobile det), recognition ~10-20 ms/line (PP-OCRv6 small
  multi-script), both through onnxruntime on the worker thread. Rec reads full-res crops
  of accepted boxes only.
- **Classifier (Step 5) — built:** geometry (size band, aspect, centredness,
  burst-with-cooldown) plus fail-open text rules (letter ratio, URL/handle) on the OCR
  text; all knobs at the top of `classifier.py`. The risk remains hand-tuning against
  real videos — knobs stay in one place, and the visible overlay highlights are the
  feedback loop for that tuning.
- **Temporal stability:** the same subtitle read on consecutive frames must be recognised as "the
  same" (avoid flicker/duplicate hotspots), and clearing must be prompt when it disappears.
