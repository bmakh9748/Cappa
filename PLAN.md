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

## On Word Click (as built)

1. The click freezes the moment: a PNG of the tracked region, the video's playback
   position (from the browser bridge), and a snapshot of the live caption list.
2. A small popup appears near the clicked word: word (edge punctuation stripped), divider,
   translation — deep-translator (free Google endpoint), the word translated IN its
   sentence for context. **NO LLM in the translation path — hard user rule.**
3. "Create Anki card" gathers the rest off the UI thread. The card's sentence is the whole
   stacked caption BLOCK (a two-line subtitle is ONE sentence, card_0031), assembled from
   the click-time snapshot reconciled with the live list at card time (`click_pool`,
   card_0045 — a sibling line detection was still re-reading at the click still joins).
4. Audio, best source first: the caption track's exact [start, end] cut from the
   downloaded source audio (works paused and on any past line) → the same caption window
   cut from the loopback ring buffer via the bridge's video-time→clock mapping → OCR-timed
   loopback. Silent clips are discarded (card_0027); every degradation leaves a note.
5. The draft saves to `cards/card_NNNN/` — word, sentence, translations, screenshot,
   audio, and `metadata.json` with full provenance (boxes, timing windows, match scores,
   video source, notes). `.apkg` export is the remaining step.

---

## Flashcard Export

- **Today:** every saved card is a complete draft folder under `cards/card_NNNN/`
  (all pieces + metadata); the export step below is not built yet.
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
| OCR (reading text) | **PP-OCRv6 small (multi-script)** via `rapidocr` + `onnxruntime` | One model reads Japanese/Chinese/English/…, ~20 ms/line; per-script packs (Arabic, …) swap in via the video-language setting |
| Overlay | **pywin32 / win32gui** | Transparent always-on-top window on Windows |
| Audio (cards) | **PyAudioWPatch** WASAPI loopback | Rolling ring buffer of what you HEAR — the only way to clip audio retroactively |
| Video source | **yt-dlp** + **ffmpeg** | Caption track (the timing oracle) + source audio download/slicing |
| Playback bridge | Chrome/Edge extension → localhost `http.server` | Which video is playing + exact position, ~1/s, never leaves the machine |
| Translation | **deep-translator** (free Google endpoint) | Word translation on click only — free, no key. **NO LLM (hard user rule)** |
| Flashcard export | **genanki** | Generates Anki `.apkg` files with embedded media |
| OS | **Windows only** (for now) | Overlay behaviour uses Win32 APIs |
| Language | **Python 3.11+** | |

### Install commands
```bash
pip install -r requirements.txt
# PySide6 pywin32 mss numpy rapidocr onnxruntime ffmpeg-python genanki
# deep-translator PyAudioWPatch yt-dlp
```

Also requires:
- **ffmpeg binary** on PATH: https://ffmpeg.org/download.html
- rapidocr downloads its ONNX models on first run (~5-15 MB per pack, automatic)
- the browser extension in `extension/` (optional): load unpacked in Chrome/Edge for
  automatic video selection + position; without it the launcher's
  "Use video from clipboard" does the same manually

---

## Key Architecture Decisions

- **No video importing** — app watches the browser window directly. Videos never enter the app.
- **OCR fires on change, not on a timer** — frame diff means OCR only runs when something on screen actually changes. Lightweight on any PC.
- **PaddleOCR over Tesseract** — Tesseract is too slow (300-800ms on full screen). PaddleOCR is built for real-time and handles the cropped change region fast.
- **Rule-based subtitle classifier, not ML** — subtitles have consistent enough traits that a scorer with 5 rules outperforms anything that needs training, and runs in microseconds.
- **Translation only on word click, never via an LLM** — deep-translator's free Google endpoint, called off the UI thread when a word is clicked. The user's explicit rule: no Claude / paid API anywhere in the translation path.
- **Subtitles can be anywhere on screen** — the change-detection approach doesn't assume a fixed subtitle region. It finds text wherever it appears on each frame change.
- **PySide6 over PyQt6** — identical API, better license for future distribution.
- **qt-material over QFluentWidgets** — QFluentWidgets looks better but adds complexity too early.

---

## Build Order

1. **Overlay window** — transparent, always-on-top PySide6 window that covers the browser **[done]**
2. **Screen capture loop** — mss capturing the locked browser window at 30fps **[done]**
3. **Frame diff engine** — numpy change detection, only flag meaningful pixel clusters **[done]**
4. **OCR integration** — neural detection + recognition on changed regions, word boxes **[done]**
5. **Subtitle classifier** — rule-based scorer to filter subtitle text from UI text **[done]**
6. **Word hotspots** — clickable transparent regions on the overlay mapped to word positions **[done]**
7. **Word click popup** — translation shown near the word (deep-translator, never an LLM) **[done]**
8. **Control panel UI** — became the corner launcher + startup/settings window **[done]**
9. **Audio extraction** — caption-track windows from source audio, loopback fallbacks **[done]**
10. **Screenshot capture** — frame frozen at word click **[done]**
11. **Flashcard export** — genanki .apkg generation **[next]**

---

## Project Layout

```
run.py                  # launcher — `python run.py` (or `python -m cappa`)
settings.json           # persisted user settings (gitignored)
cappa/
  __init__.py           # version / package marker
  __main__.py           # `python -m cappa`
  app.py                # Qt setup + main(): startup window -> overlay + launcher
  winapi.py             # ALL Win32/ctypes/DWM — no Qt (input, windows, click-through)
  translate.py          # word -> translation (deep-translator/Google; no Qt; cached;
                        #   the word is translated IN its sentence via a marked span)
  settings.py           # tiny persisted settings holder (settings.json; no Qt)
  audio.py              # LoopbackRecorder: WASAPI loopback ring buffer (90s, monotonic-
                        #   stamped, follows the default output device; fail-soft; no Qt)
  ui/                   # everything the user SEES
    overlay_window.py   # OverlayWindow: paint, pick/select modes, follow loop, worker wiring
    launcher.py         # corner icon + menu: pick/select/deselect · clipboard video ·
                        #   settings · exit; status tooltip (target · fps · captions · yt)
    startup.py          # startup window = the settings home (languages, clip sliders);
                        #   reopened live via the launcher's Settings… item
    word_popup.py       # the box a clicked word opens: word · divider · translation · Anki btn
  detection/            # everything that FINDS captions (see its __init__ for the map)
    capture.py          # screen grab (mss -> numpy BGRA)     every frame  ~10ms   [done]
    diff.py             # what changed since last frame       every frame  <1ms    [done]
    stability.py        # watch live captions for vanishing   every frame  <1ms    [done]
    detector.py         # NEURAL text detection (PP-OCRv5 ONNX) on change  ~60ms   [done]
    tracking.py         # ledger: live captions, judged junk, drift/blip machinery  [done]
    classifier.py       # caption or not-caption (geometry + text rules)           [done]
    worker.py           # background QThread chaining the stages, emits results
    ocr.py              # read text in accepted boxes (PP-OCRv6 ONNX + per-script
                        #   packs picked by the video-language setting)  ~20ms      [done]
    sentence.py         # Sentence/Word model, caption BLOCKS (stacked lines), and
                        #   click_pool (click-time vs card-time caption reconcile)  [done]
  flashcard/            # gathers one card's pieces into cards/card_NNNN (no .apkg yet)
    builder.py          # build_draft: block sentence, audio source choice, snap-to-track
    model.py            # CardDraft
    provenance.py       # verify the clicked word really belongs to its sentence
    screenshot.py       # click-time PNG capture/write
    timing.py           # audio window maths (detection lags, pre/postroll, min/max clip)
    writer.py           # card_NNNN folders + metadata.json
  source/               # YouTube caption-track source: exact timing + source audio
    vtt.py              # WebVTT parser (manual + auto rolling formats) -> timed tokens
    transcript.py       # Transcript model + OCR-line -> caption-window aligner
    youtube.py          # yt-dlp fetch of metadata/captions/audio + ffmpeg slicing
    session.py          # SourceSession: the active video, fetched on a daemon thread
    bridge.py           # localhost http server the browser extension reports to
extension/              # "Cappa Bridge" Chrome/Edge extension (load unpacked): which
                        #   YouTube video + position, POSTed to 127.0.0.1:8765 only
cards/                  # saved card drafts (gitignored)
tests/
  run_all.py            # the whole suite; units first, then live tests
  test_diff/classifier/tracking/watcher/merge.py   # detection units, instant, windowless
  test_ocr_read.py, test_ocr_arabic.py             # recognition through the real models
  test_flashcard.py, test_audio.py                 # card drafts, timing, loopback recorder
  test_youtube_source.py, test_bridge.py           # VTT/alignment/session, browser bridge
  test_settings.py, test_translate.py              # settings roundtrip, word cleanup
  test_overlay_*.py, test_captions_live.py         # live overlay behaviour
  test_browser_sim.py, test_realistic_video.py     # end-to-end sims (draw what they detect)
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
> gone. `ui/launcher.py`: a 46px translucent rounded icon (the Cappa logo — 1b "Caption
> tile" from the logo explorations: red #D0433B tile, two caption bars, painted by
> `ui/logo.py` from the 128px design geometry; the state dots sit on small dark
> discs so red dots survive the red tile) parked bottom-left of the primary screen.
> The same logo is the app icon: `logo.app_icon()` on the QApplication covers the
> startup/settings title bar and alt-tab, and for the TASKBAR (this Win11 build
> ignores window icons — python.exe's icon showed no matter what) `app.py` renders
> `%LOCALAPPDATA%\Cappa\Cappa.ico` and installs a Start Menu "Cappa" shortcut
> carrying our AppUserModelID (`winapi.install_start_menu_shortcut`), which is
> where the taskbar takes a group's icon from; side benefit, Cappa is searchable/
> pinnable in Start. Fail-soft, and note the icon may only appear from the second
> launch (the shell indexes the shortcut after the first button is created).
> The launcher icon sits always on top, Parsec-style. Clicking it pops a dark menu with exactly
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
> **Word hotspots (#6) + placeholder popup (done, user-directed).** The loud debug boxes
> are gone: captions stay undecorated until the cursor reaches for one. `ocr.py` returns
> per-word geometry alongside the text — the rec model's own word grouping, CTC column
> spans mapped back to frame px (mapping verified visually on rendered lines): real words
> for spaced scripts, kanji-block/kana-run groups for Japanese — the PROVISIONAL click
> unit until the tokeniser-vs-Claude-call decision is made. The ledger carries words with
> each live caption (`captions()`; the worker emits payload[1] = [(box, words)], words
> survive blip resurrection). Overlay: hovering a word highlights it (translucent cyan,
> pointing cursor), clicking opens `ui/word_popup.py` — dark box above the word (below if
> cramped) with the word text and a ✕ that closes it. Word rects + the open popup are
> interactive rects, so click-through lifts over them and clicking a word can NEVER
> click/pause the video underneath; all input is polled on the tick like the other modes.
> The popup closes on pick/select/idle/park. Fallback: if word geometry is unavailable
> the whole line is one hotspot; if rec is down there are simply no hotspots (captions
> still tracked). Hover style: a barely-there white lift over the word + a crisp accent
> underline (link-hover look). A glyph-exact hue tint (Otsu stroke mask per word, tint
> painted through it) was BUILT AND REJECTED — on real compressed video the masks go
> ragged and the effect looks patchy (user-tested); don't retry it without adding mask
> cleanup (morphological close) AND testing on real H.264 first. For step #7 note: the full line text comes back from read() — store it
> on the ledger when the Claude call needs "word + sentence". Known flake to watch: one
> 0xC0000409 teardown crash of test_browser_sim in one suite run (Qt/onnxruntime exit
> race); passed standalone and on rerun.
>
> **Sentence/Word model + word-geometry fix (done, user-reported).** Hotspots often
> landed BETWEEN words: the spans were built from fixed margins around each word's CTC
> emission columns, and emission points drift within glyphs. Fixed by midpoint
> partitioning in `ocr._word_spans` — boundaries sit halfway between adjacent words'
> edge-character centres so the spans TILE the line (no gaps to land in; the tiling is
> asserted in test_ocr_read), and the line's outer edges are bounded by the median
> character pitch, not the det box (a roomy det box must not give the first/last word a
> hotspot over background). Verified visually on rendered en+ja lines. Same pass added
> `detection/sentence.py`: a read caption line is now a `Sentence` (text, box, words) of
> `Word`s (text, box, back-ref to its sentence) — ocr.read returns (Sentence, conf), the
> ledger stores Sentences (`captions()` returns them, payload[1] = [Sentence]), overlay
> hotspots carry Word instances, and the popup keeps `popup.word` — so the Claude call
> (#7) gets word + full sentence straight off the clicked Word, and the Japanese
> word-unit decision (tokeniser vs Claude) swaps in at exactly one place: how
> Sentence.words is built.
>
> **Stylised/off-centre captions in a user area (done, screenshot-driven).** A user
> screenshot (Indonesian gaming video: big yellow italic caption + blue "APA?" shout,
> top-left of frame) exposed two stacked failures. (1) The strict geometric rules
> rejected the stylised lines even INSIDE a Select-area region ("too tall" / aspect /
> off-centre). `classifier.filter` now takes `user_area` (worker passes its provider at
> scan time): in a user-drawn region position rules don't apply — no centredness, no
> height cap, aspect floor 2.5→1.3 (`USER_AREA_MIN_ASPECT`) — the drawn box IS the
> position statement; the size floor and burst rule stay. (2) DBNet fragments big
> spaced/italic text into per-word boxes — six passed at once and the burst rule killed
> the whole batch. `detector.merge_lines()` (every mode) now glues fragments that
> overlap vertically and sit within a glyph-height's horizontal gap: one box per text
> line, which is what the ledger/classifier/watcher/OCR all assume. Verified against
> the real screenshot end-to-end: whole-window accepts only the centred "Live chat
> Ivan :" line (conservative by design), Select area accepts and reads all three
> ('"1 kata buat Manca, Van!"' 0.96, 'APA?' 0.99). Tests: tests/test_merge.py +
> user-area legs in test_classifier. The screenshot lives untracked in the repo root
> (*.png is gitignored).
>
> **No 'too big', and big text is position-free everywhere (done, user rule +
> screenshots).** Two more screenshots (fullscreen "BELOK!" ~122px; windowed YouTube
> with a glowing "LOH?" ~101px) — window/fullscreen tracking rejected stylised captions
> that Select area accepted. User rules now encoded: (1) the height cap is DELETED —
> there is no such thing as a too-big caption; (2) text taller than
> max(36px, 6.5% of region height) skips position rules in EVERY mode (loose aspect
> 1.3): chat/UI junk is smaller than that, stylised captions bigger — 6.5% sits above
> page furniture (video titles/headers ~5-6% of a browser window; the browser sim's
> baseline-quiet assertion guards this boundary, and it caught 4% as too permissive)
> and below every screenshot caption (7-12%). Verified full-frame: screenshot 1 both
> stylised lines read (the small "Live chat Ivan :" label is Select-area-only now),
> "BELOK!" reads at 1.00, and on the browser window the live flow works (baseline mutes
> page text; the next caption line is accepted alone — cold-scan bursts don't apply to
> live tracking). User screenshots live in `screenshots/` (gitignored via *.png).
> IMPORTANT testing note (recurring confusion): in window mode a caption already on
> screen at lock-on is baseline-muted until the NEXT line — testing window mode on a
> paused video looks like "never works". Select area judges immediately.
>
> **Lock-on UX round (done, user-directed).** Windowed-browser tracking is structurally
> the weakest mode (video is ~half the window, so captions are proportionally small and
> off-centre with the pane — two more screenshots confirmed). Three changes: (1) a
> 6-second on-overlay tip after Pick window — "fullscreen the video, or Select area over
> it" — reusing the pick/select instruction rendering (`_tip_until`); (2) the baseline
> scan now JUDGES big pre-existing text instead of memorising it (`_scan(baseline=True)`
> keeps only `classifier.big_text` boxes from the first scan; small text is still
> memorised unjudged — furniture safety): a stylised caption already on screen at
> lock-on is boxed immediately, which was the user's thrice-hit complaint (incl. the
> browser sim's deliberately-quiet FIRST line — that line is small, so the sim's
> assertion stands and its comment now explains the split; test_area_rescan gained
> big-vs-small window-mode legs — note the DET BOX height is the label/backdrop strip,
> not the glyphs, when sizing test fixtures). (3) screenshots checked: fullscreen
> captions all work; windowed-pane captions below the big-text bar remain
> window-mode-rejected (off-centre) — that's what the tip is for, per the user's own
> call ("prompt the user to either full screen or select an area").
>
> **Word popup content (#7, first slice) + accept-all detection (done, user-directed).**
> Clicking a word now opens the real popup: the word with edge punctuation/symbols
> stripped (`translate.clean_word`, Unicode categories — 「」, ♪ and commas go, inner
> marks stay: don't), a divider line, the translation underneath, and a "Create Anki
> card" button that enables once a translation exists (the card flow behind it lands
> stepwise next — the button is a stub today). Translation is `cappa/translate.py`:
> **deep-translator's free Google endpoint — NOT the Claude API. Hard user rule
> ("do not use Claude to translate"), encoded in the module docstring: no LLM in the
> translation path, ever.** No key, no per-click cost; source auto-detected per word,
> target `en` (constant until the settings panel); results cached per word. The popup
> opens instantly with "Translating…" and fills via a helper thread + queued signal
> (request-id guard drops stale fills; the popup re-clamps when it grows); failures
> (no internet) show as a ⚠ line and keep the card button disabled. Smoke-tested
> end-to-end with a stubbed translator (both legs) + tests/test_translate.py for the
> cleanup. ALSO: **accept-all detection experiment** (user report: too many real
> words rejected, sometimes between consecutive lines; hover-only styling makes
> loose detection cheap). `CaptureWorker(accept_all=True)` — flipped on in
> overlay_window._start_capture, one arg to revert — stands down ALL caption gates:
> geometry/position/burst/cooldown (classifier.filter early-out), the OCR text
> rules, AND the window-mode baseline muting; every text line the detector finds is
> OCR'd and becomes hoverable words (junk text is hoverable too — that's the deal).
> The strict path is untouched (default accept_all=False) and still what the sims
> and unit tests assert; new accept_all leg in test_classifier. Known suspect if
> words STILL go missing after this: a box whose OCR read comes back empty/None
> goes live with ZERO word hotspots (Sentence("", box, []) in worker._scan) and is
> never re-read while it lives — a retry-the-read lever, not built yet.
>
> **Refresh / re-scan on demand (done, user-directed).** The user was nudging the
> window size to make detection look again; now there's a real control. `CaptureWorker.refresh()`
> flips an atomic bool that the worker loop consumes on its next pass: it drops ALL
> detection memory (live captions, seen/memorised furniture, pending clears, burst
> cooldown — `watcher/classifier/ledger.reset()`) and forces one judged scan RIGHT NOW
> (baseline=False, last_scan=0), then emits even with no events so the overlay's stale
> hotspots are replaced. Crucially it does NOT reset the diff (that would re-trigger the
> first-frame branch and re-baseline/re-mute) — it keeps capturing, just re-judges. Wired
> two ways: a **"Refresh words"** launcher menu item (enabled only while tracking, shows
> its shortcut) and a global **Ctrl+Alt+Shift+R** hotkey (same triple-modifier scheme as
> quit — nothing in a browser collides; edge-detected in the overlay tick so holding it
> fires once). New `winapi.VK_R`; launcher ctor gained an `on_refresh` arg. Covered by a
> 4th leg in test_area_rescan: baseline memorises the small window-mode line, then a
> refresh boxes it (verified live — `read small pre-existing line of text (0.99)`).
>
> **Flashcard drafts — the card pipeline behind the button (#9 + #10, done).**
> `cappa/flashcard/` + `cappa/audio.py`. Audio can't be captured after the fact, so
> `LoopbackRecorder` (PyAudioWPatch) records the default output device's WASAPI
> *loopback* — what you hear — into a rolling 90 s ring buffer, every chunk stamped
> with `time.monotonic()`, the SAME clock the worker stamps caption appear/clear times
> with; a sentence's timestamps index straight into the buffer. It follows the default
> output device (the originally-bound endpoint dying when the user switches outputs
> produced silent clips) and is fail-soft: no library / no device just leaves
> `ready=False` and cards without audio. `build_draft` gathers everything into
> `cards/card_NNNN/`: cleaned word, the caption-block sentence, both translations,
> the click-time PNG, the audio clip, and `metadata.json` with full provenance —
> word/sentence boxes, `sentence_verified` (provenance.py flags a clicked word that
> isn't really in its sentence), the audio window with its source and match score, the
> video source, and `notes`: every degradation (no recorder, no match, silent clip,
> failed translation) is recorded on the card instead of raised. Timing model in
> `timing.py`: appear/clear detection lags, pre/postroll, and MIN/MAX_CLIP (a card
> studies ONE word — 3 s cap however long the line sat on screen; 1 s floor so a blip
> is still audible), user-tunable live. A clip whose int16 peak is ≤100 is silence
> (muted tab / wrong output device — card_0027): discarded with a note, because a
> silent wav on a card is worse than no audio.
>
> **YouTube caption-track source — the timing oracle (done).** Live-capture timing was
> the flaky part (detection latency, lags, the line already half-spoken); the fix is
> staged, and stage 1+2 are in. The on-screen OCR stays the source of a line's *text*
> (works even when a video has no captions at all); `cappa/source/` supplies the
> *timing*: `vtt.py` parses WebVTT (manual + auto rolling formats) into timed tokens,
> `transcript.py` aligns an OCR line to the track by similarity, `youtube.py` fetches
> metadata/captions/audio with yt-dlp (lazy import; manual track preferred over auto)
> and slices with ffmpeg, `session.py`'s `SourceSession` holds the active video —
> fetched on a daemon thread, fail-soft (`status`/`error`), transcript published as
> soon as it parses so a card made before the audio download lands still gets exact
> timing provenance. Card audio priority: the matched caption window cut from the
> DOWNLOADED source audio (works paused and on any past line) → the same window cut
> from the loopback buffer via the bridge's video-time→clock mapping → OCR-timed
> loopback as before. Window choice (`builder._choose_window`): the playback position
> is the boss — a text match is trusted outright only when strong (≥0.75) AND near the
> position; a WEAKER text match still wins when it overlaps the position window,
> because it spans the on-screen SENTENCE where the position window is just the speech
> chunk around the click (card_0044 clipped mid-sentence on a garbled auto-caption);
> a text match that doesn't overlap where we are is not trusted. A strong match
> against a HUMAN-made track also corrects OCR misreads word-for-word
> (`_snap_to_track`, card_0018: a punctuation glyph read as an alif poisoned the
> translation) — never from auto captions, never for dissimilar words.
>
> **Browser bridge + "Cappa Bridge" extension (done).** `source/bridge.py`: a tiny
> threaded `http.server` on 127.0.0.1:8765. The extension (`extension/`, manifest v3,
> load-unpacked) snapshots `{videoId, currentTime, paused, title}` ~once a second on
> youtube.com and POSTs it there — nothing leaves the machine. The bridge keeps the
> latest (monotonic-stamped; position extrapolates between updates) plus a short
> HISTORY: `mono_at(video_t)` maps a video timestamp to the monotonic moment it played
> through the speakers — what lets a card cut caption-exact audio from the LOOPBACK
> buffer when the source download isn't ready. It also receives the user's youtube.com
> cookies (→ Netscape file for yt-dlp, whose anonymous fetches trip YouTube's bot
> check). Extension hardening: v1.1 survives its service worker being replaced, v1.3
> only the VISIBLE tab reports (two open videos used to fight over the bridge). With
> the extension the video auto-selects; without it the launcher's **Use video from
> clipboard** (paste a YouTube URL) is the manual path. Fail-soft: port taken = bridge
> stays down with an `error`, app runs as before.
>
> **Settings + startup window (done).** `cappa/settings.py` persists `settings.json`
> (missing/corrupt file = defaults; no Qt); `ui/startup.py` is a normal titled window
> shown at launch and reopened LIVE via the launcher's **Settings…** item — it is the
> settings home going forward: add a field in settings.py + a row in startup.py.
> Fields today: **target language** (what clicked words translate INTO, default en) and
> **video language** (what the user is learning; "auto" keeps per-word auto-detect,
> naming it fixes lone-word detection) — the video language also picks the OCR rec
> model (`ocr._SCRIPT_MODELS`): the default multi-script pack cannot read Arabic/
> Cyrillic/Devanagari/Korean, so per-script rapidocr packs swap in live (Arabic
> verified end-to-end; RTL hotspot words un-mirrored; spaces the rec model drops
> between words rebuilt from word geometry). Plus **clip length sliders** (min/max)
> wired to `timing.set_clip_bounds` — module globals read at call time, so a change
> applies to the very next card.
>
> **Word-in-sentence translation (done).** `translate.py` translates the clicked word
> INSIDE its sentence (the word marked with guillemet-style quotes, translated, the
> marked span extracted from the result) so polysemous words resolve by context; fix
> on top: a sentence containing the marker characters itself must not hijack the
> marked span. The popup shows this contextual translation; both card translations go
> through the same path. Still deep-translator/Google only — **no LLM, hard user
> rule.**
>
> **Detection: caption blocks + drift + click_pool (done, card-driven).** The cards
> folder doubles as the bug tracker — each fix cites the card that exposed it.
> (1) card_0031: a two-line subtitle is two ledger Sentences, so a card carried half
> the caption. `sentence.caption_block()` groups stacked rows (same glyph height,
> adjacent, aligned — transitive, capped at 3 lines) and `CaptionBlock` quacks like
> one Sentence for the builder; the popup translates with the whole block as context.
> (2) The popup freezes the moment at CLICK time: screenshot, playback position, and
> the live caption list — so a caption clearing while the popup sits open can't
> hollow out the card. (3) c5bb718: a live caption REPLACED IN PLACE (next line, same
> spot, no clean vanish) sat stale and unclickable until a manual refresh — the ledger
> now fingerprint-watches live boxes (`drifted()`, 0.30 s confirm so control-bar
> gradients don't retire real captions) and the same scan re-reads the new text;
> FP_TOLERANCE tightened 14→8 (a new line over a remembered spot must never read as
> "unchanged"). (4) card_0045: clicked in the half-second the ledger spent re-reading
> the TOP line of a fresh two-liner — the click snapshot held only the clicked line,
> and the card lost the line above even though it was plainly in the click screenshot
> and passed stacking geometry (measured: heights 63/65 px, gap 17 px vs the 56.7 px
> limit). NOT an audio-length issue (the audio window is derived FROM the sentence
> text, never the reverse) and NOT the merge geometry: the line simply wasn't in the
> ledger at the click instant. Fix: `sentence.click_pool()` — the card is built
> seconds after the click, so while the clicked line is still live the CURRENT list
> is the base (late siblings join) and snapshot lines only fill rows no live line
> occupies; once the clicked line is gone, the screen has moved on and the snapshot
> governs alone. Residual gap, known: click during the churn AND the caption clears
> before "Create" is pressed → the snapshot alone still misses the sibling; the fix
> would be fingerprint-checking pending clears in the ledger before surfacing them.
>
> **UI rounds (done).** Launcher menu grew **Use video from clipboard**, **Settings…**
> and **Deselect window** (stop tracking without exiting — back to idle); the tooltip
> status line shows the caption source (`yt: <state>`), so whether cards will get
> caption-track timing is visible at a glance. The popup reports degraded saves on the
> button itself ("Saved card_0046 · 2 notes") instead of a silent thumbs-up.
>
> **Next step:** the genanki `.apkg` export (the last build-order item — the drafts
> in `cards/` already contain every field and media file a card needs), plus the
> Japanese word-unit decision (local tokeniser vs resolving the clicked segment).
>
> _Deferred / known limits:_ multi-DPI across
> mixed-scaling monitors may be slightly off (uses primary-screen DPR); tracking a
> **non-maximized** window on Win11 includes its rounded corners, so a few desktop pixels
> behind it bleed into the region — background activity there (cursor blink etc.) adds
> occasional noise triggers (region clustering should reject these; revisit then);
> a live box whose OCR read comes back empty has ZERO word hotspots and is never
> re-read while it lives (retry-the-read lever, not built); loopback history is 90 s —
> a word clicked later than that has no fallback audio (the caption-track path doesn't
> care); the card_0045 residual gap above (churn + clear before "Create").
>
> **Card-quality fixes (done, from the cards 47–62 debugging night).** Audio downloads:
> YouTube format URLs now carry a JS challenge — `_ydl_opts` enables the deno/node
> runtimes and requirements moved to `yt-dlp[default]` for the solver scripts.
> Translation: ALL-CAPS hardsubs are lowercased before Google (KELAR read as caps came
> back 'GONE'; lowercase 'finished' — card_0052), answers keep natural casing (user
> call), and a marked-span answer whose back-translation names a DIFFERENT word of the
> sentence is rejected for the bare word (quotes drifted onto a neighbour, card_0047).
> Privacy: the loopback recorder pauses when the extension stops reporting a visible
> YouTube tab and resumes on return (`_gate_recorder`; extension-less sessions keep the
> always-on recorder). Blocks: stacked caption rows whose det boxes BLEED (outline/glow
> fonts overlap up to ~45% of row height) still join one block (card_0052); OCR reads
> each line twice with different croppings and the spacier agreeing read wins (the rec
> model's space emission flips with framing at these font sizes — card_0056). Audio
> windows: rolling-VTT token ends absorb the following silence, so position-window
> grouping now caps effective word duration (card_0061's 'lagi' "lasted" 8.2 s and
> blocked the walk back to the sentence start), and a position-matched clip anchors at
> the caption's on-screen APPEARANCE mapped to video time (`bridge.video_at`), not at
> wherever playback sat when clicked. Honesty: a card built from a shaky OCR read
> (conf < 0.8) says so in its notes (card_0060's unreadable page produced a
> confident-looking garbage card).

## The polish stage — better reading and translation (planned, user call)

Three upgrades queued from real failures, all opt-in and none breaking the free/local
default path:

1. **DeepL as the primary translator** (free tier 500k chars/month; Indonesian and
   Arabic supported). Same deep-translator dependency surface, but the API takes an
   explicit `context` parameter — replacing the quote-marking hack and its drift
   failure mode entirely. Google's free endpoint stays as the fallback; the no-LLM
   rule stands.
2. **Cloud OCR as "hard mode"** for text the local models cannot read. Ground truth
   from card_0060: fully-vocalized classical Arabic (aldiwan poetry page) is beyond
   every PP-OCR arabic pack — v5 mobile reads mush at conf 0.45, server packs don't
   exist, v4 mobile reads mirrored. Google Cloud Vision (the Lens engine; ~free at
   our volume: 1k images/month free) or Azure Read (5k/month free) reads it clean.
   Trigger: `ocr_conf` below the shaky threshold (now measured per line) or a
   settings toggle; local PP-OCR stays the always-on reader.
3. **Page-text source for non-video pages**: extend the bridge extension beyond
   youtube.com so a normal webpage POSTs its visible text + word geometry, and pages
   like the poetry site never need OCR at all. This is the correct fix for card_0060,
   not a better model.

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
