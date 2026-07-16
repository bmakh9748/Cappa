# Video Flashcard Tool — Project Plan

This file is the project's BUILD LOG and idea history: what was built, what
was measured, and what was tried or weighed and set aside — so nothing gets
re-attempted blind. Read it for the story; for the current rules read
[AGENTS.md](AGENTS.md) (the structural contract) and each package's
`__init__.py` docstring (the authoritative file maps). Where this file and
the code disagree, the code wins.

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
   then the word's MEANING — Wiktionary definitions first (`cappa/dictionary.py`,
   free REST API, senses ordered so the one agreeing with the in-context
   translation comes first; cards 65/66: Google's contextual trick mistranslates
   grammatical glue), falling back to deep-translator's free Google endpoint with
   the word translated IN its sentence. For JAPANESE the popup is a reader's
   dictionary entry instead — headword 【reading】, part-of-speech tags, numbered
   senses, the inflection chain — resolved offline from the JMdict pack
   (`cappa/jmdict.py`), no network call at all. **NO LLM in the translation/dictionary
   path — hard user rule.**
   In Japanese a hotspot is one CHARACTER and the WORD is whatever the dictionary
   says the character belongs to; dragging across characters forces a span by hand.
3. "Create Anki card" gathers the rest off the UI thread. The card's sentence is the whole
   stacked caption BLOCK (a two-line subtitle is ONE sentence, card_0031), assembled from
   the click-time snapshot reconciled with the live list at card time (`click_pool`,
   card_0045 — a sibling line detection was still re-reading at the click still joins).
   The gathered draft goes NOWHERE by itself — it opens the preview (step 6).
4. Audio: the row's OBSERVED on-screen life is the timing backbone (inverted
   2026-07-07 — the YouTube track is only a precision bonus when strong, near and
   human-made; auto-ASR kept picking neighbour lines and had 20s holes). Anchors
   must be honest: an appearance seen during steady playback, or the row's logged
   first sighting from an earlier watch (`transcripts/`), or the track's position
   window near the click. The clip is cut from the downloaded source audio → the
   loopback ring buffer via the bridge's time mapping → OCR-timed loopback.
   Silent clips are discarded (card_0027); every degradation leaves a note.
5. The draft saves to `cards/card_NNNN/` — word, sentence, translations, screenshot,
   audio, and `metadata.json` with full provenance (boxes, timing windows, match scores,
   video source, notes).
6. The PREVIEW opens on it (`ui/card_preview.py`): every piece the card will carry,
   on the front/back faces the Flashcards settings chose, with the draft's notes and a
   playable clip. **Add to Anki** delivers it; **Discard** deletes the draft folder.
   Nothing reaches Anki until the user says so.

---

## Flashcard Export

- Every saved card is a complete draft folder under `cards/card_NNNN/` (word,
  sentence, translations, screenshot, audio, `metadata.json` provenance).
- **No separate export step.** Clicking **Create Anki card** saves the draft
  and shows it in the preview; **Add to Anki** there puts it into Anki in one
  background-thread pass
  (`cappa/flashcard/anki_sync.py` — the whole feature in one module). Anki
  OPEN: the card goes through the AnkiConnect add-on's localhost API
  (2055492159) and is visible in the running app the moment the button
  finishes. Anki CLOSED: it's written straight into Anki's own collection
  file (the official `anki` package) and is there on next launch. No
  .apkg, no import dialog either way; if Anki can't be reached at all the
  button says so.
- **Delivery is per-card and once.** A delivered folder gets an
  `anki_synced.txt` receipt and is never touched again — whatever the user
  edits or deletes IN Anki stays that way; a sync pass sends only
  receipt-less cards (normally: exactly the card just saved). A failed
  delivery leaves no receipt and rides the next save; a card already in
  Anki (found by its `cappa::card_NNNN` tag) is adopted, not re-added. One
  deck per learning language ("Cappa \<Language\>", get-or-create by name).
  This sweep is why **Discard deletes the draft folder** (`writer.discard_draft`):
  a rejected card left on disk would ride the next save into Anki.
- Each card contains: word, translation, sentence, screenshot, audio clip —
  whichever of those the Flashcards settings tab has switched on, on
  whichever side, in the design configured when that card was made
  (`card_template` in its own metadata).

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
| Video source | **yt-dlp** + **ffmpeg** | Caption track (timing precision bonus — the SCREEN is the backbone since 2026-07-07) + source audio download/slicing |
| Playback bridge | Chrome/Edge extension → localhost `http.server` | Which video is playing + exact position, ~1/s, never leaves the machine |
| Word meanings | **Wiktionary REST** (`dictionary.py`) + **deep-translator** (free Google endpoint) | Definitions first, contextual translation as hint/fallback — free, no key. **NO LLM** |
| Flashcard export | **AnkiConnect** (live, Anki open) + **anki** (direct collection write, Anki closed) | One module (`flashcard/anki_sync.py`) picks whichever shows the card soonest; `anki` is the official library the desktop app itself runs on |
| OS | **Windows only** (for now) | Overlay behaviour uses Win32 APIs |
| Language | **Python 3.11+** | |

### Install commands
```bash
pip install -r requirements.txt
# PySide6 pywin32 mss numpy rapidocr onnxruntime anki
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
11. **Flashcard export** — per-card Anki delivery riding the Create card button **[done]**
12. **Card draft review (user correction)** — before a card is synced, a DRAFT
    popup shows exactly what the card will contain (word, sentence, both translations,
    the screenshot, the audio clip) so the user can SEE it and fix what the pipeline got
    wrong — the wrong-word OCR read, a sentence polluted by on-screen furniture, a clip
    that landed off the word. The human-in-the-loop that makes the automatic mistakes
    recoverable instead of silently shipped to Anki.
    **[preview done 2026-07-09 — see the entry below; EDITING the fields is the next
    slice]**

---

## Project Layout

```
run.py                  # launcher — `python run.py` (or `python -m cappa`)
settings.json           # persisted user settings (gitignored)
AGENTS.md               # the structural contract (read it before changing code)
cappa/
  __init__.py           # version / package marker
  __main__.py           # `python -m cappa`
  app.py                # Qt setup + main(): startup window -> overlay + launcher
  winapi.py             # ALL Win32/ctypes/DWM — no Qt (input, windows, click-through)
  translate.py          # word -> translation (deep-translator/Google; no Qt; cached;
                        #   the word is translated IN its sentence via a marked span)
  dictionary.py         # word -> MEANING: Wiktionary definitions, contextual
                        #   translation as hint + fallback (no Qt, no LLM);
                        #   Japanese goes to jmdict.py instead
  jmdict.py             # Japanese: JMdict pack (sqlite) + deinflection rules.
                        #   word_at(line, char) resolves the character under the
                        #   cursor to the whole word — the tokeniser Japanese needs
  lexicon.py            # per-language word list (downloadable ~50k pack) + the
                        #   splitter that un-glues OCR'd words (CANALWAYS->CAN ALWAYS)
  settings.py           # tiny persisted settings holder (settings.json; no Qt)
  audio.py              # LoopbackRecorder: WASAPI loopback ring buffer (90s, monotonic-
                        #   stamped, follows the default output device; fail-soft; no Qt)
  ui/                   # everything the user SEES
    overlay_window.py   # OverlayWindow: paint, pick/select modes, follow loop, worker wiring
    source_wiring.py    # the video-source machinery behind the overlay: bridge +
                        #   session + recorder + OCR transcript log (Qt-free; ticked)
    launcher.py         # corner icon + menu: pick/select/deselect · clipboard video ·
                        #   settings · exit; status tooltip (target · fps · captions · yt)
    startup.py          # startup window = the settings home, two tabs: Languages
                        #   and Flashcards (what a card holds, front/back/off, design)
    template_dialog.py  # advanced card-design editor (front/back HTML + CSS)
    logo.py             # the Cappa logo as paint code; window/taskbar icons
    word_popup.py       # the box a clicked word opens: word · divider · meaning · Anki btn
    card_preview.py     # what the built card holds, before delivery: front/back faces,
                        #   notes, playable clip; Add to Anki | Discard (deletes draft)
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
    sentence.py         # Sentence/Word model (a CJK Word is one CHARACTER; span_word
                        #   fuses the range a dictionary lookup spanned), caption
                        #   BLOCKS (stacked lines), click_pool (click-time vs
                        #   card-time caption reconcile)                           [done]
    latency.py          # the pipeline's measured reaction times (appear/clear lags)
  flashcard/            # gathers one card's pieces into cards/card_NNNN, and
                        #   puts each new card into Anki (anki_sync.py)
    builder.py          # build_draft: block sentence, provenance, translations,
                        #   snap-to-track correction — what the CARD says
    clip.py             # the audio: window choice (seen life vs track) + cutting
    model.py            # CardDraft
    prefs.py            # which fields a card collects and on which side (live copy)
    template.py         # Anki-style card template: HTML faces + CSS, default design
    provenance.py       # verify the clicked word really belongs to its sentence
    screenshot.py       # click-time PNG capture/write
    timing.py           # audio window maths (pre/postroll, min/max clip)
    writer.py           # card_NNNN folders + metadata.json; discard_draft deletes a
                        #   draft the preview rejected (else the sweep would sync it)
    anki_sync.py        # sync(): put the new card into Anki -- live (AnkiConnect)
                        #   when open, collection file when closed; per-folder
                        #   anki_synced.txt receipts, delivered once, never re-swept  [done]
  source/               # video-source truth: timing, audio, and Cappa's own transcript
    vtt.py              # WebVTT parser (manual + auto rolling formats) -> timed tokens
    transcript.py       # Transcript model + OCR-line -> caption-window aligner
    youtube.py          # yt-dlp fetch of metadata/captions/audio + ffmpeg slicing;
                        #   media cache, self-pruned to 500 MB at startup
    session.py          # SourceSession: the active video, fetched on a daemon thread
    bridge.py           # localhost http server the browser extension reports to;
                        #   honest mono<->video time mapping + steadiness probe
    ocr_transcript.py   # Cappa's OWN transcript: every caption row it watched,
                        #   appended to transcripts/<video>.jsonl; sighting recall
extension/              # "Cappa Bridge" Chrome/Edge extension (load unpacked): which
                        #   YouTube video + position, POSTed to 127.0.0.1:18765 only
cards/                  # saved card drafts (gitignored) — ALSO the bug tracker
transcripts/            # per-video JSONL of every caption row watched (gitignored)
tests/
  run_all.py            # the whole suite; units first, then live tests
  test_diff/classifier/tracking/watcher/merge.py   # detection units, instant, windowless
  test_ocr_read.py, test_ocr_arabic.py             # recognition through the real models
  test_flashcard.py, test_audio.py                 # card drafts, timing, loopback recorder
  test_youtube_source.py, test_bridge.py           # VTT/alignment/session, browser bridge
  test_settings.py, test_translate.py              # settings roundtrip, word cleanup
  test_jmdict.py        # Japanese: deinflection, longest match, the screenshot's clicks
  test_word_popup.py    # the live drag preview commits nothing; the release commits
  test_overlay_*.py, test_captions_live.py         # live overlay behaviour
  test_browser_sim.py, test_realistic_video.py     # end-to-end sims (draw what they detect)
  test_anki_sync.py     # per-card Anki delivery (fake add-on + throwaway collection)
  test_card_preview.py  # preview contents; Discard deletes, a receipted card survives
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
> (the `bench_paddle_*.py` scripts that still wanted paddle were deleted 2026-07-08).
> New tests:
> `test_ocr_read.py` (real Japanese+English reads through TextReader), text-rule legs in
> `test_classifier.py`, hysteresis legs in `test_tracking.py`. Suite: 11/11.
>
> **Word hotspots (#6) + placeholder popup (done, user-directed).** The loud debug boxes
> are gone: captions stay undecorated until the cursor reaches for one. `ocr.py` returns
> per-word geometry alongside the text — the rec model's own word grouping, CTC column
> spans mapped back to frame px (mapping verified visually on rendered lines): real words
> for spaced scripts, kanji-block/kana-run groups for Japanese — the PROVISIONAL click
> unit until the tokeniser-vs-Claude-call decision is made. (**Superseded 2026-07-09f**:
> the CJK grouping was the bug, not the click unit — hotspots are per character now and
> `jmdict.word_at` finds the word.) The ledger carries words with
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
> hotspots carry Word instances, and the popup keeps `popup.word` — so the meaning lookup
> (#7) gets word + full sentence straight off the clicked Word, and the Japanese
> word-unit decision swaps in at exactly one place: how Sentence.words is built.
> (It did, 2026-07-09f: for CJK a Word became one character.)
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
> **Next step:** EDITING in the card preview (build-order #12's second half —
> the preview itself shipped, see 2026-07-09e), then the deferred timing
> reconciliation. (Correction, same day: an earlier edit of this line called
> the Japanese word-unit decision "closed, its other option was a Claude
> call". Wrong — the LOCAL half was always live, and it landed hours later
> as `cappa/jmdict.py`. See 2026-07-09f.)
>
> _Deferred / known limits:_ multi-DPI across
> mixed-scaling monitors may be slightly off (uses primary-screen DPR); tracking a
> **non-maximized** window on Win11 includes its rounded corners, so a few desktop pixels
> behind it bleed into the region — background activity there (cursor blink etc.) adds
> occasional noise triggers (region clustering should reject these; revisit then);
> a live box whose OCR read comes back empty has ZERO word hotspots and is never
> re-read while it lives (retry-the-read lever, not built); loopback history is 90 s —
> a word clicked later than that has no fallback audio (the caption-track path doesn't
> care); the card_0045 residual gap above (churn + clear before "Create");
> skipping to a caption NEVER watched appearing has no knowable start — the
> clip is an honest window around the click with a note (user-accepted,
> card_0007), and recall takes over once the line has been seen once;
> extension-less sessions have no mono→video mapping, so their transcript-log
> rows carry null video times and can't feed recall.
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
>
> **Flashcards settings tab + card design (done).** The startup window grew a
> second tab: each card field (word, word translation, sentence, sentence
> translation, screenshot, audio) is placed on the card's FRONT, BACK, or OFF —
> off means the piece isn't even gathered (no screenshot, no audio cut, no
> translation call). `flashcard/prefs.py` holds the live copy the builder
> reads; `template.py` regenerates a default Anki-style design (HTML faces +
> CSS) from the placements, and `ui/template_dialog.py` is the advanced editor
> for a custom template (kept saved, used only while "custom design" is on).
> Audio OFF also stands the whole video machinery down — no recorder, no
> auto-select, no downloads ("if i'm not using audio i really don't need you
> to track the video") — and the launcher's yt dot goes dark instead of lying:
> a green dot must mean "the next card gets caption-exact audio".
>
> **Word meanings: Wiktionary first (done, cards 0065/0066).** Google's
> in-context trick answers "what does this word do in THIS sentence's English
> rendering," which reads wrong for grammatical glue (YANG → "sees") and fused
> phrases (Ngupil → "picking her nose" — the whole predicate). `dictionary.py`
> asks English Wiktionary's REST API for per-language definitions (free,
> key-less, same no-LLM rule), orders senses so the one agreeing with the
> contextual translation comes first, and falls back to that translation when
> no entry exists. Needs the video language NAMED in settings + an English
> target; otherwise it's a pass-through to translate().
>
> **Bridge off port 8765; caption track by language (done).** 8765 is
> AnkiConnect's port — and Anki is exactly the app a Cappa user has open.
> Windows lets two reuse-flagged sockets share a port, so whichever app
> launched first silently ate the extension's reports (the failure came and
> went with reboots). The bridge is on 18765 now and binds EXCLUSIVE, so a
> taken port fails loudly into the tooltip instead of silently sharing. Also:
> the caption track is picked by LANGUAGE first, manual-ness as tiebreak.
>
> **2026-07-07 — the timing inversion: the screen is the backbone (done,
> cards 0075-0082; user decision, don't re-litigate).** The YouTube track
> stopped being the timing oracle: auto-ASR kept matching neighbour lines and
> had holes 20s+ wide at the exact clicks. New priority: the caption's
> OBSERVED on-screen life anchors the clip; a track text match is trusted
> only when strong, near the click, AND from a human-made track; auto matches
> yield to the position/on-screen machinery. Same push: matched windows cap
> the phantom silence tail (card_0075); a click in an ASR silence belongs to
> the line that already PLAYED, measured from its capped end (card_0077); a
> mid-life click on a PLAYING video waits ~2s for the row's clear — the clear
> is the sentence's true end where the click is just where the mouse got
> there first (cards 0077/0082); a row the track never heard is clipped from
> its on-screen life alone (card_0080); failed caption fetches retry with a
> cooldown instead of staying red for the whole watch. `flashcard/clip.py`
> split out of builder.py (the audio half: window choice + cutting). And
> Cappa now writes its OWN transcript: `source/ocr_transcript.py` appends
> every caption row that leaves the screen to `transcripts/<video>.jsonl`
> with its life in both clocks — the durable record cards cite, and the base
> the recall feature (below) reads back.
>
> **AGENTS.md + structure round (done, same day).** AGENTS.md became the
> structural contract (read order, package maps, Qt boundaries, the no-LLM
> and no-self-credit rules, cards-as-bug-tracker, commit style); every
> package `__init__` docstring holds its file map. Refactors:
> `detection/latency.py` now owns APPEAR_LAG/CLEAR_LAG (they measure the
> pipeline, and `source/` no longer imports `flashcard/`);
> `detection/__init__` stopped importing the worker, so no-Qt packages reach
> sentence/latency without dragging PySide6 in; `ui/source_wiring.py` took
> the ~200 lines of bridge/recorder/session/log orchestration out of
> overlay_window.py (966 → 770 lines) — Qt-free on purpose, so a future
> reading mode without a tracked screen region (the page-text plan) can
> reuse it whole.
>
> **The nine-card field test (done, 2026-07-07 evening — the fresh cards/
> numbering).** Cards made deliberately under every playback condition: play
> through, pause mid-life, seek to a caption, click a paused frame, rewind
> and re-click. Every wrong clip traced to ONE root cause: trusting "when
> the row popped onto the screen" as "when the sentence started" after a
> pause or a seek. Fixes, each with a regression test named for its card:
> (1) `bridge.video_at` anchors on the samples BRACKETING the moment and
> never extrapolates — a stamp 0.8s into a pause used to map 1.1s past where
> the video sat (card_0005), a paused seek mapped 34s into the abandoned
> stretch (card_0007); paused stretches map to their frozen position, a seek
> hidden between brackets returns None. (2) `bridge.steady_at`: an
> appearance anchors a clip only if the video REACHED that moment without a
> jump — a pause beginning moments later is fine (pausing to click IS the
> card-making motion), seeks and deep-pause pops are refused with a note on
> the card. (3) A trusted appearance now BOUNDS the position window's start
> (backshift 1.0s + grace 0.8s): ASR run-ons dragged 4s of the previous
> sentences into cap-length windows that ignored the centre ("started too
> early by a lot", cards 3/5). (4) RECALL: when the live stamp is refused,
> `window_hint` reads the transcript log back and anchors at the row's
> EARLIEST mapped sighting near the click — "you should have saved where the
> caption popped up": it did, and watch → rewind → pause → click now works
> from memory, across sessions. Stacked-caption rows match by containment (a
> block IS its rows, which live and die together — card_0016 logged 'AKU
> CUMA' + 'BSie DITONTON, LHO!' as two rows), with a 6-char floor so junk
> rows ('C') can't lend timing, and a sighting whose life spans a seek keeps
> its start but surrenders its end. A garbage ASR token with a CORRECT
> timestamp is still a good position anchor (card_0016's track text was
> literally "C" — timing right, text worthless — and the clip was perfect).
> Also that day: the media cache (downloaded audio, 10-54 MB/video — 176 MB
> had piled up) prunes itself to 500 MB at startup, oldest first; cards/ and
> transcripts/ are records, never pruned.
>
> **Ideas weighed and set aside — so they aren't re-tried blind.**
> (1) *Word→all-timestamps map + small ML model to pick sentence spans* —
> declined 2026-07-07: the map is the Transcript re-keyed (identical
> information, sorted by word instead of time), the clustering search IS what
> `window_for` already computes, a learned scorer has no training data, and
> the videos that actually hurt have cross-language or holey tracks — the
> sentence's words aren't in the map at all, so no model on top can recover
> them. The AUDIO-side version is queued instead (VAD, polish item 4): the
> missing information lives in the waveform, not the text.
> (2) *Webpage reading modes* (manga/image OCR "hard mode"; HTML right-click
> "make sentence") — deferred until the Anki export ships; HTML mode first
> when resumed (the bridge extension already exists, and it is the true fix
> for card_0060-type pages).
> (3) *Custom caption-detection model* — still declined; crop-classifier
> first if precision ever demands ML.
> (4) *Glyph-exact hue tint on hover* — built and rejected (ragged masks on
> compressed video); don't retry without morphological cleanup tested on
> real H.264.
>
> **Getting cards into Anki — the road to `anki_sync.py` (done).** Build
> order item 11 first shipped as a `genanki` `.apkg` exporter, then as a
> launcher "Export to Anki" menu action writing `collection.anki2`
> directly. Both are gone: the user wanted no export control and no import
> dialog of any kind, so the sync fused to the Create Anki card button
> itself ("when i press make card it goes straight to anki that is what i
> want"). What survived those rounds and still holds: write through Anki's
> own API, never hand-rolled SQL; back up `collection.anki2` before
> touching it (it holds thousands of notes of unrelated study history);
> one deck per learning language; identify each note by a `cappa::card_NNNN`
> tag. See the entry below for the code as it actually stands.
>
> **2026-07-08 — Anki sync goes live and per-card, all of it in
> `flashcard/anki_sync.py` (done; user calls).** First: "when i press
> create card... i should see it immediately in anki" — the direct
> collection write can never do that (Anki locks `collection.anki2` while
> it runs), so the user green-lit an add-on, and AnkiConnect (2055492159)
> was ALREADY installed on this machine (the playback bridge moved off
> port 8765 precisely because AnkiConnect owns it). sync() probes the
> add-on's port per call (1s cap — it sits on the card-save thread) and
> picks its route: Anki open → the card goes through the localhost JSON
> API (pure stdlib urllib, no new dependency) and appears in the running
> app as the button finishes; closed → written into the collection file
> with the official `anki` package (rotating backup first), there on next
> launch. Both routes write identical identity — the `cappa::card_NNNN`
> tag, one "Cappa card" notetype (created once from the current design;
> the copy in Anki is the user's to edit), per-card media names
> (`card_NNNN_screenshot.png`) — so either route adopts the other's notes.
> Second: "just the card i saved, not all the cards" — a sweep that
> re-sends every draft resurrects anything the user deletes in Anki (they
> deleted the whole backlog deck minutes after it appeared; the deck is
> theirs). Delivery is per-card and ONCE: a delivered folder gets an
> `anki_synced.txt` receipt and is never touched again; a failed delivery
> leaves no receipt and rides the next save; a card already in Anki is
> adopted by tag, not re-added; deleting a receipt is the manual re-sync
> for one card. The old unwired `.apkg` bundling path and its genanki
> dependency were removed the same day. All existing card folders were
> receipted in the migration (the deck deletion stands — nothing re-adds
> it). Verified twice against the real running Anki: the 20-draft backlog
> landed live in "Cappa Indonesian" on the first pass, and after the
> receipts change a scratch dir holding one receipted + one new card
> delivered EXACTLY the new one, in 0.2s (the test then erased its own
> traces). `tests/test_anki_sync.py`: closed route against a throwaway
> collection, open route against a faked add-on at the module's transport
> seam, receipts/adoption/no-resurrection.
>
> **2026-07-08 — recall is session-only, and a sighting closes where it
> really ended (done, card_0025; user rule, don't re-litigate).** Two
> defects in the same call. (1) `window_hint` read `transcripts/*.jsonl`
> back off disk, so a card made today could take its timing from a watch
> weeks ago. Banned: "you can take from only this instance of the app being
> open. when you close the app it should go. when i reopen taking from the
> past times its been open is bad." The log keeps a per-run in-memory list
> of what it watched and answers only from that; the files stay a durable
> record and are never read. A reopened app recalls nothing until it
> watches something itself. (2) A hardsub that TYPES ON is re-read as it
> grows, logging a chain of rows — card_0025's line logged as `NUMPANG` →
> `NUMPANG DI HELIKOPTER ..` → `NUMPANG DI HELIKOPTER KEBALIK .`. Recall
> took the earliest row for its START (right: a seek can only log a LATER
> appearance, so the minimum is nearest the true pop) but also took that
> row's END — which is merely the moment the fragment grew. The clip closed
> at 205.983 when the caption lived to 207.073: **1.09 s lost, ending
> before "DI HELIKOPTER KEBALIK" was ever spoken.** A sighting now closes
> at the last row of its chain, where "same chain" is ≤ `SIGHTING_GAP`
> (2 s) of wall clock — in the real log a chain's rows follow within
> 0.03-1.13 s while the nearest re-watch sits 3.3 s away, so the two never
> blur. Replayed against the real transcript, the recall returns
> 205.518 → 207.073. `tests/test_ocr_transcript.py` grows two legs: a
> reopened log recalls nothing from its own file, and a typed-on line
> closes at its last row while a re-watch never extends it.

> **2026-07-08 — junk is clickable, never card-able (done, card_0028).** The
> first Shorts cards came out as `@korrathetaymi -DIED ON-THE`: the channel's
> watermark sits one row above the caption, matches its glyph height, and
> `caption_block` stacked it into the card's sentence. It is NOT a classifier
> miss — `text_verdict` already calls a leading `@` a handle, and the
> watermark reads at **confidence 1.000**. The rule simply never runs: the
> overlay passes `accept_all=True` (user call — "junk text becomes hoverable
> words, nothing more"), which stands down every gate including the text
> rules. Read literally, that promise was about HOTSPOTS, not about what may
> land on a card. So the worker now always computes `text_verdict` and, under
> accept_all, STAMPS the row (`Sentence.junk`) instead of dropping the box.
> A stamped row stays clickable, but never joins someone else's caption block
> and never enters `transcripts/`. Clicking the watermark itself still works
> — that's a deliberate act. Measured on the same frame: the caption reads at
> 0.960, the watermark at 1.000, the channel name `ComedyShot` at 1.000 and
> is TALLER than the caption (79px vs 62px) — so neither confidence nor size
> can separate chrome from captions, which is why the text rules exist.

> **2026-07-08 — Shorts polish pass (done, on real cards card_0027
> `YOU CANALWAYS` and card_0028 `@korr -DIED ON-THE`):**
> 1. *Reject chrome.* The junk-stamp above (`Sentence.junk` from the text
>    rules) already catches the common watermark by its shape — card_0028's
>    '@korrathetaymi' is an @-handle. A repetition-based FURNITURE detector
>    was tried for plain-text chrome (a bare channel name) and REMOVED the
>    same day: it conflated three things it couldn't tell apart — a genuine
>    catchphrase said twice, and worst, a REWOUND caption. Rewinding to
>    re-study a line is the exact workflow window_hint serves (card_0009),
>    and furniture-by-repetition would have flagged that very line as junk.
>    Plain-text chrome is deferred to the positional approach (queued item 6:
>    the extension reports the video/caption rect, everything else is masked
>    — deterministic, no heuristic).
> 2. *Word un-gluing by lexicon* (replaced a short-lived cross-frame voting
>    hack). The rec model is confident-wrong about spacing on tight captions
>    (card_0027 'YOU CANALWAYS'). A first attempt read the crop at a second
>    padding and unioned the two framings' spaces — user rejected it as a
>    lucky case that can also over-split when a framing hallucinates a stray
>    space. The real fix (`cappa/lexicon.py`): split a glued token ONLY into
>    pieces that are every one a real word, from a per-language word list;
>    never tear a genuine word, leave an OCR letter-error ('YOUCAL') alone.
>    `ocr._lexicon_split` decides WHAT to split, `_respan` positions the
>    hotspot boxes. One downloadable ~50k-word pack per language
>    (FrequencyWords, cached in `lexicon_packs/`, fetched by the worker like
>    the OCR models, no-op without a pack). Verified end-to-end with the real
>    en pack: card_0027's crop reads 'YOU CAN ALWAYS', three hotspots, a
>    hover sweep picks exactly one word at every x.
> 3. *Sentence tail by words-left, this video's pace.* `_live_end` no longer
>    adds a flat 0.4s to a line still on screen; it predicts the end as
>    APPEARANCE + words × pace (user call: don't assume the clicked word is
>    the one being spoken — the user reads, then clicks). Pace is measured
>    per video from its own captions (`ocr_transcript.seconds_per_word`,
>    median life/words over multi-word rows), clamped, defaulting until 5
>    rows are seen. Legal because the clip comes from the downloaded file;
>    the loopback rescue still clamps (a ring buffer holds no unplayed audio).
> 4. *Clip cap raised 5s -> 8s* (`AUTO_MAX_CLIP`, MAX_CLIP_RANGE): a real
>    spoken sentence is 2.1s median, 7.0s p90, so 5s truncated one in five.

> **2026-07-09 — transcript only records a live video; + fast-caption
> groundwork (done + a design set).** Two logging bugs: (1) a PAUSED video's
> frozen frame produced appeared_video==cleared_video artifacts; (2) worse,
> when the extension's reports went stale (tab hidden/closed/not YouTube)
> the last video id PERSISTED, so on-screen text from another tab/app got
> logged under it. Both fixed by one gate: `source_wiring.live_video_id`
> returns the id ONLY on a fresh, playing bridge report, else None, and
> `ocr_transcript.observe` logs nothing without an id (test_source_gate.py).
> Also instrumented: each transcript row now records `ocr_conf` (additive
> key) to settle whether captions too quick to read come back
> low-confidence — early read (cards 0027/0028 garbles at 0.96-1.00) says
> NO: OCR reads confidently wrong or MISSES entirely (no event, no
> confidence), so confidence can't be the trigger for "OCR fell behind".
> BUILT the same day — word-at-a-time sentence completion (user spec; the
> tried-and-rejected alternatives are logged in memory, not here): a clicked
> chunk finds its whole SENTENCE in the track (`Transcript.sentence_for`:
> text match is the gate — burned-in creator subs don't match and are left
> alone; `sentence_slice` grows the match to terminal punctuation / '>>'
> speaker markers / a >1.5s silence). `builder._complete_sentence` then
> rebuilds the sentence OURS-FIRST (`_fill_sentence`): our transcript rows
> are the words wherever we read them; the track fills only unclaimed
> words; a low-`ocr_conf` row loses its say (each row now logs conf); a row
> claims a track word only when TIME-near AND TEXT-similar (0.5) — time
> alone let 'RESPECT' swallow 'you' and let a watermark spanning the
> sentence claim real words; rows claiming nothing are dropped (the
> neighbour sentence's words, 'SUPER TE' chrome). Timing: ours at any edge
> we cover, the track's otherwise; the span rides to the audio cut as
> matched_by='assembled' (clip.py skips the clear-wait — the end is known).
> Provenance: metadata gains `sentence_assembly` {span, score,
> ocr_sentence, filled_from_track}. Verified on the real g4erZWrKEjQ track
> + transcript: 'years'→'two YEARS ago', 'team'→'you on that TEAM',
> one-word sentences untouched. `tests/test_sentence_fill.py`.

Upgrades queued from real failures, all opt-in and none breaking the free/local
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
4. **VAD clip-edge snapping**: a tiny local voice-activity model (silero-vad,
   ~2 MB ONNX on the already-present onnxruntime — free, offline, no keys)
   snapping a chosen audio window to real speech boundaries in the downloaded
   source audio. Language-agnostic, so it works exactly where text matching is
   hopeless (cross-language auto-dub tracks, ASR holes): trims run-on starts
   to the speech onset, closes unknown ends at the silence. This is the
   accepted half of the ML idea weighed on 2026-07-07 (see "Ideas weighed and
   set aside") — the model reads the waveform, where the missing information
   actually lives.
5. **YouTube Shorts support. [Built 2026-07-13 — see that entry; live
   grounding on a real short still owed.]** The pieces that *look* like
   blockers already work:
   `extension/content.js` matches `/shorts/<id>` and picks the largest PLAYING
   `<video>` (card_0018's fix, when the feed's preloaded neighbours were being
   read at t=0), and `youtube.extract_video_id` accepts `/shorts/` URLs, so
   metadata, captions and audio fetch unchanged. **The real blocker is the loop.**
   Everything in the timing stack assumes video time advances with the wall clock
   between seeks, and a looping short breaks that in four places:
   - `bridge.mono_at(video_t)` — "when did video second `t` play through the
     speakers" — picks the sample with the nearest `currentTime`. Under a loop,
     video time is no longer *injective* over wall clock: second 3.0 exists once
     per pass, and the nearest-sample search may answer with a pass from minutes
     ago (outside the loopback ring buffer). This is the one that silently cuts
     the wrong audio. Fix: stamp each sample with a **pass counter** (bumped when
     `currentTime` jumps back while the previous sample sat near `duration`), and
     resolve `mono_at` within the newest pass that contains `video_t`.
   - `bridge.steady_at(mono)` — a loop restart reads as a seek (correctly), but it
     poisons the whole `STEADY_BEFORE` (2 s) window behind it, so no caption in a
     pass's first two seconds can ever anchor its own appearance. With the pass
     counter, "steady" can mean "no jump *within this pass*".
   - `tracking.py` identity — the same caption, same pixels, same box, one loop
     later. If it never registers a clear, `appeared_at` keeps a stamp from the
     previous pass and maps to nonsense. A loop boundary should force-clear the
     live ledger: the screen belongs to a new pass.
   - `ocr_transcript.REPEAT_GAP` (10 s) — written to stop a paused frame spamming
     the log with the same row. A short under 10 s loops *inside* that window, so
     every pass after the first is silently discarded as a "blip loop". The
     dedupe must exempt a genuine loop boundary.

   Two smaller ones: clip windows are never clamped to `[0, duration]` (the
   duration is already in `session.meta()`, just unused), and scrolling the feed
   rewrites the video id every ~700 ms report, so a fast scroll fires a fetch and
   an audio download per short — debounce on "same id for ≥1.5 s". Finally, many
   shorts carry **no caption track at all**, which throws the whole card onto the
   on-screen life — so the loop work above is a prerequisite, not a polish pass.
   Order: pass counter in the bridge → loop-clear the ledger → dedupe exemption →
   duration clamp + id debounce. Ground it first by making one card on one short
   and reading its `metadata.json` provenance, the way every other timing bug here
   was found.
6. **Positional chrome masking.** Reject watermarks, channel bars, buttons and
   YouTube's own translated-subtitle overlay by POSITION, not by reading them. The
   content script already calls `getBoundingClientRect()` on the active `<video>`;
   have it also report that rect (and, when present, the `.ytp-caption-window-
   container` rect) to the bridge. The app masks detection to the video area and
   drops anything over the player chrome — deterministic, no heuristic, and it
   NEVER reads the overlay's words (position only, so the no-ASR/no-LLM rules are
   untouched). This is the real fix for the plain-text chrome that a repetition
   heuristic could not safely catch (furniture detection was tried and removed
   2026-07-08: it flagged rewound captions, which is the opposite of what a study
   tool should do). Cost: mapping browser client coords → screen coords.
7. **Lexicon word-splitter — DONE 2026-07-08** (`cappa/lexicon.py`, was
   "stage 5"). Splits a glued token into pieces that are every one a real word,
   ranked by frequency, ≤3 pieces, each ≥2 chars, positioned via `ocr._respan`.
   Skips CJK (no spaces). Word source: a downloadable ~50k-word pack per language
   (FrequencyWords / OpenSubtitles, cached in `lexicon_packs/`), fetched by the
   detection worker like the OCR model packs — the "user downloads a pack per
   language" model the user chose. Cannot fix an OCR letter error ('YOUCAL', N
   misread as L). Remaining polish if ever needed: a Settings toggle / progress
   note while a pack downloads; Arabic clitics (والبيت) are naturally safe because
   a bare clitic isn't a standalone pack word, so no split is proposed.

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
- **Classifier (Step 5) — built, then DELETED (2026-07-09, user call; see the
  entry below):** the geometry gates, baseline muting and 'seen' memory rejected
  too many real words; accept-all had long been the shipped behaviour. All that
  remains is `classifier.text_verdict`, the junk-text STAMP (clock/URL/handle
  stays clickable but off cards/transcript, card_0028).
- **Temporal stability:** the same subtitle read on consecutive frames must be recognised as "the
  same" (avoid flicker/duplicate hotspots), and clearing must be prompt when it disappears.

---

## 2026-07-09 — The great simplification (user calls, all three)

Three deletions in one day, all user-ordered after real cards kept coming out
wrong for reasons buried in accumulated machinery:

1. **Caption-vs-not gates deleted.** See the Step 5 bullet above. `worker.py`
   lost the baseline/user-area/accept_all plumbing, `tracking.py` lost the
   'seen' memory (provably dead once nothing is ever rejected) and with it the
   paused-frame `ignore_seen` rescue it necessitated. `classifier.py` is now
   just the junk-text tag.

2. **Blanket clip padding deleted.** `APPEAR_BACKSHIFT` (1.0) +
   `APPEAR_TRIM_GRACE` (0.8) opened every position-anchored clip 1.8 s before
   the appearance stamp — worst-case insurance sized from one bad card pair
   (0069/0071) that put the previous sentence's tail on every card with an
   accurate stamp, which is most of them: the same row watched three times
   stamped within ~170 ms (card_0035's transcript). The measured lags in
   `detection/latency.py` (already subtracted at mapping time) and the user's
   own buffers are the only time arithmetic left.

3. **The clip-window ladder replaced by THE WINDOW RULE (user spec, verbatim
   intent: 'it is not complicated').** The clip is the clicked caption BLOCK's
   on-screen life: every row of the block looked up in this run's transcript
   (`sighting_window` — which also recalls the real pop when the live row is a
   churn REBIRTH, card_0002, or a seek landing) plus its live ledger stamps;
   EARLIEST appearance → start, LATEST clear → end, `HEAD_BUFFER` 0.4 s in
   front, `TAIL_TRIM` 0.2 s off the end (both user-specified). The caption
   track NEVER times a clip anymore — `choose_window`, position windows,
   `_onscreen_match`, the slide-to-click invariant and the steady-seek gate
   are all gone; the track's text match is recorded only as provenance and as
   `_snap_to_track`'s ground truth (card_0018 unchanged). min/max clip
   settings still clamp, the cap centring on the click. The sentence-assembly
   span (word-at-a-time fill) still wins when it exists — it is transcript
   timing already.

   card_0002 was the trigger: animated eye-strokes drawn beside 'melahirkan'
   kept churning the row (clear + re-accept + '-I' misread), the reborn row's
   stamp mapped to 40.569 for a caption whose logged sighting popped at 39.11,
   and the no-padding anchor missed the clicked word entirely. Under the rule
   the same card cuts 38.71→40.37. Detection-side root cause (watch/fingerprint
   the GLYPHS, not the det box, so adjacent animated art can't churn the row)
   is diagnosed but NOT yet built — the transcript-recall anchor makes the
   clip immune either way.

Tests: `test_classifier.py` is junk-tag-only; `test_area_rescan.py` and
`test_browser_sim.py` assert the accept-everything world (caption-zone-scoped);
`test_youtube_source.py`'s builder tests all re-derived for the window rule
(deleted: choose_window / position-fallback / distrusts-impossible-stamp /
backshift tests). Full suite re-run green, live sims 5/5 styles zero FP.

### 2026-07-09c — the caption track becomes a bounded fallback (card_0006)

Two follow-on card fixes on top of the window rule:

- **No buffer around the on-screen life.** The transcript's appeared/cleared
  are ALREADY detector-lag-corrected (ocr_transcript subtracts APPEAR_LAG;
  the live-stamp path subtracts it too), so the earlier HEAD_BUFFER 0.4 /
  TAIL_TRIM 0.2 double-counted — a head buffer opened the clip early and the
  tail trim chopped the last word (card_0006: 'jangan banyak alasan ya' lost
  its tail). Both constants are now 0 (kept named for a future deliberate
  ring-out).
- **The track fills a missing edge and extends the END, but never moves the
  START earlier than our observed appearance.** window_for (word-exact,
  de-phantomed, same-occurrence-gated) supplies an edge we never saw and
  extends the end when our clear came early (churn / a quick click loses the
  tail). But the caption's words weren't on screen before we saw it pop, so
  an EARLIER track tag is just the ASR's lead and must not pull the clip onto
  the previous sentence (card_0006: our clean appear 49.05 vs the auto
  track's 'jangan' at 48.42). Provenance now records start_from / end_from /
  track_window so which source timed each edge is auditable.
- Min-clip verified still enforced under Auto (a 0.537s life widens to the
  0.6 floor; Auto only frees the MAX). No change needed there.

### DEFERRED — the reconciliation: whose timing to trust (open)

The core unsolved question, to return to. Our on-screen timing is usually the
most accurate, but it fails specifically when **the caption came and went too
quickly for our scanner to read** (churn, a pause, a typed-on line, a flash) —
then it's a fragment and the autocaption's window is better. The task is to
know WHEN our reading is a fragment and switch sources per edge.

- REJECTED referee: sentence length D (block words × pace). The failure is
  about our detection's TIME RESOLUTION, not how long the sentence is — a fast
  long line and a slow short line fail on the same thing (did it linger long
  enough for us to pin both edges). D can't tell us that. (User call.)
- DIRECTION: the tell is our observed on-screen time vs a FIXED floor tied to
  our scan cadence (~SCAN_INTERVAL + APPEAR/CLEAR lag). Under it, we flashed-
  missed it → use the autocaption; above it → trust ours. Open: measure it as
  video-time duration, or add a real scan-count to the transcript row; and
  whether DETECTION stamps the "barely-saw-it / timing-unreliable" flag at
  watch time (it sees the frames) so the builder just reads the flag.
- The user's fuller ladder for genuinely-missing edges, once the tell exists:
  saw start not end → end = earliest-block-appearance + D (block words, update
  with the real clear if it later lands); saw end not start → start = end − D;
  saw neither → the single autocaption cue at the click (timing only, text may
  be garbage), unless that cue < ~0.6·D → ; no autocaption → note it and clip
  = [click − D, click + D] (~2× length, since the click's place in the
  sentence is unknown). Text-match trust gated by a similarity check (~0.6).
- Current INTERIM behaviour (this section's card_0006 rule) assumes our
  appearance is reliable; it gets card_0004 wrong (our appear was itself a
  churn fragment there, so the track's earlier start was right) — that's the
  case the fragment-tell will fix.

### 2026-07-09d — sentence translation: flat join, not comma (user-reported)

"My cat gave birth" was coming out "I gave birth to a cat". Not a translator
problem — Google renders the plain sentence perfectly ("tadi kucing saya
melahirkan" -> "My cat just gave birth"). The bug was our own: a multi-line
caption block was sent to the translator COMMA-joined ("tadi kucing, saya
melahirkan"), and the stray comma made Google parse two clauses -> "the cat,
I gave birth". The comma-join was an over-fit to card_0074 (one caption that
happened to wrap at a clause boundary); it breaks the far more common
mid-clause wrap. Now the sentence is translated FLAT (space-joined, exactly
as the card displays it). card_0074 reads a touch more awkwardly flat but is
still correct; every mid-clause wrap is fixed. NO change to the no-LLM rule —
Google was never the problem. (`builder._translate_fields`.)

### 2026-07-09e — the card preview (build-order #12, first slice)

`ui/card_preview.py`. Create Anki card no longer delivers anything: it builds
the draft as before, then opens a preview of it and stops. The window shows
every piece the card will carry, grouped under FRONT and BACK exactly as the
Flashcards settings placed them — word, both translations, the sentence, the
click-time screenshot, the clip (a ▶ Play button, `winsound`, no new
dependency) — with a dim "— no audio" / "— no screenshot" line where a piece
is missing and the draft's notes as ⚠ lines. That absence is the point: a
silent clip dropped by card_0027's rule, or a shaky-OCR note, is now visible
BEFORE the card ships. Two exits: **Add to Anki** (the old sync, moved here)
and **Discard**.

**Discard has to delete the folder.** `anki_sync.sync()` delivers every
`card_*` folder without an `anki_synced.txt` receipt, so a previewed-then-
rejected draft left on disk would ride the NEXT card's save into Anki —
silently resurrecting exactly what the user just rejected, which is the bug
the per-card receipts were introduced to kill (2026-07-08). `writer.discard_draft`
deletes it, and REFUSES a folder that already carries a receipt: that card is
in Anki, and Anki's copy is the user's. Same reason there is no "leave it for
later" exit — the ✕ discards, and a draft superseded by another Create click
is discarded rather than abandoned. A FAILED delivery still keeps the old
behaviour (no receipt, rides the next save) and keeps Discard reachable.

Structure: the preview is a TOP-LEVEL window, not an overlay child like the
word popup — a Select area can be far narrower than a screenshot needs. It is
excluded from capture like the overlay and launcher (detection must never read
our own UI), and the overlay counts its hwnd as "ours" (`popup.roots()` →
`preview.roots()`) so opening it doesn't park the tracking border. The
`_built` signal carries the draft with NO request-id guard, deliberately: a
built draft exists on disk and must be resolved whatever the popup moved on
to. Editing the fields — the half of #12 that lets the user FIX a wrong read —
is the next slice. `tests/test_card_preview.py`.

### 2026-07-09f — the Japanese word unit: the DICTIONARY is the tokeniser

The oldest open question in this file, answered. A Persona 5 screenshot: the
card came out for the fragment 戻, the translations were nonsense, and the
user asked for highlight-to-select or "words that just didn't get split up
like that". Measured on the real frame, the OCR was **innocent** — 戻るのも面倒
なんで read at conf 1.00. The hotspots were the crime:

    戻 | るのも | 面倒 | なんで          (script-run grouping)
    オマエやっといてくれ | 。            (one 11-char hotspot: all kana)

The recogniser groups characters by SCRIPT RUN, and the kanji→kana boundary
is where okurigana lives. It is not approximately wrong; it is
anti-correlated with word boundaries, cutting 戻る, 面白い, 食べる at exactly
the one place a Japanese word never breaks. A kana-only line has no boundary
at all, hence the 11-character blob.

**What YomiNinja actually does** (the tool the user pointed at) is not
segment. It renders the OCR text as selectable text and lets Yomitan/10ten
do the work; those do rule-based deinflection with many dictionary queries
per word, scanning forward from the cursor. The `~ru Godan/u-verb, intrans.`
line in the user's screenshot IS the deinflection result. So: **stop
splitting at OCR time, resolve the word at LOOKUP time.**

- `detection/ocr.py` emits ONE SPAN PER CHARACTER on CJK lines (the
  midpoint-tiling maths was already per-character; the grouping step was
  simply deleted). Spaced scripts keep real words. Hangul is spaced, so
  `_spaceless` (respace/lexicon guard) still covers it while `is_cjk`
  (per-character hotspots) does not.
- `cappa/jmdict.py` — the whole feature in one file: the pack, ~100
  deinflection rules, and the scan. `resolve(text, i)` tries substrings from
  `i` longest-first, each raw AND through every form it could be an
  inflection of, first type-consistent JMdict hit wins. `word_at(text, i)`
  additionally looks BACK up to 8 characters and keeps the longest match
  COVERING `i` — Yomitan can't do that (it has no character hotspots), and
  without it a click on 倒 in 面倒 answers "reverse; inversion".
- `sentence.span_word()` fuses the matched character range into one Word
  carrying `.lemma`; `script_span()` reproduces the old grouping as the
  no-pack fallback, so nothing gets worse without a download.
- The popup is a reader's entry now: headword 【reading】, POS tags, numbered
  senses, and the inflection chain (食べられなかった — past negative,
  negative). **Zero network calls on the Japanese path.**
- The card studies the DICTIONARY FORM (`draft.word` = 戻る) and records what
  was on screen (`word_surface` = 戻って, additive metadata key). Provenance
  matches the resolved span by (line, char offset) since it is no longer
  identity-present in `sentence.words`.
- Drag across characters to force a span (the user's ask) — the hand-picked
  surface is still deinflected, so dragging 戻って teaches 戻る. Clicks now
  open the popup on RELEASE, since a press is also the start of a drag.
- **The definition follows the drag, live** (`popup.preview_for`, user ask):
  every time the selected span changes, the popup re-renders the entry for
  it. The preview is deliberately side-effect free — it never freezes the
  click moment (no screenshot, no playback position, no caption snapshot),
  never touches the network, and leaves Create Anki card DISABLED
  (`popup.word = None`): only the release commits, through show_for. A
  Japanese hit is a cached sqlite lookup (0.6 ms), so it is affordable per
  tick; a span the dictionary has no entry for says so and waits, because
  a translation per drag tick would be a network call per pixel. The popup
  anchors to where the drag STARTED, so it doesn't chase the cursor, and
  click-through is pinned OFF for the whole drag (like a region resize) so
  a selection dragged off the caption can't scrub the video underneath.
  Selections are always ≥2 characters — dragging back onto the first one
  collapses the selection and previews the resolved word again.
  (Superseded 2026-07-10: a drag now starts on cursor TRAVEL, not on
  crossing a character boundary, so a single character is a legitimate
  selection — see that entry.)

Three ranking bugs found by testing every character of the screenshot's
lines, each fixed in the pack build: (1) わかった resolved to 分かつ ('to
divide') because the ambiguous godan past った was tried つ before る — the
ambiguous rules (って/った/んで/んだ) are now ordered by row frequency and the
first that HITS the dictionary wins, so 待った still finds 待つ; (2) オマエ
found nothing until katakana was folded to hiragana; (3) 本 answered 元
('origin') and に answered 荷 ('baggage') until entries were ranked by
whether the queried form is their HEADWORD, and 「の」 displayed as 乃 until
the headword preferred kana over kanji spellings JMdict tags rare/sK.

DATA: JMdict, © the Electronic Dictionary Research and Development Group,
used under their licence — attribution required, and it is in README.md. The
jmdict-simplified JSON release (11.4 MB) is downloaded once by the detection
worker beside `lexicon.ensure_pack` and converted to a 48 MB stdlib sqlite3
database (217,768 entries, 496,335 keys, ~9 s), so lookups are indexed and
memory stays flat. Free, key-less, offline afterwards, **no LLM** — the same
rule translate.py and dictionary.py live by. Uncached `word_at` is 0.6 ms;
the overlay caches per (line, char).

Not done: JMnedict (proper names — 10ten's third tab; 佐倉 in that scene
resolves as common nouns); pitch accent; a Settings toggle while the pack
downloads. `tests/test_jmdict.py` pins all 16 screenshot clicks and SKIPS
cleanly when no pack is present.

### 2026-07-10 — selection: persistent, visibly its own act, one character fine

Three user calls on the day-old drag interaction, one bug found under them:

- **The highlight stays.** The committed word or selection keeps its
  highlight while its popup is open (`_active_word`), instead of vanishing
  the moment the button came up or the cursor moved — the screen and the
  popup now agree on what was picked. It dies exactly with its popup, and
  with its caption (`_on_regions` drops it when the Sentence leaves the live
  list: the popup keeps its click-time snapshot, but a wash painted over raw
  video would lie).
- **Selection looks like selection.** Its own graphic
  (`_draw_word_selection`: translucent accent wash + solid outline — reads
  as selected text) plus an I-beam cursor for the whole drag; the fleeting
  link-hover underline is untouched. Not the glyph-exact tint (tried and
  rejected 2026-07-08); a filled box needs no stroke mask.
- **A single character is a legitimate selection.** A drag used to begin
  only when the cursor crossed onto ANOTHER character, so nothing smaller
  than one hotspot could ever be picked and dragging back to the origin
  collapsed to the resolved word. A drag now begins on cursor TRAVEL
  (`DRAG_START_PX`, 6 px from the press point — even inside the pressed
  character), so 倒 can be dragged out of 面倒 and gets its own entry
  (JMdict heads it 逆しま). Below the threshold a release is still a plain
  click on the resolved word.
- Bug under the selection maths (shipped 2026-07-09, latent): the span was
  sliced to the last hotspot's START index — right for CJK where every
  hotspot is one character, but an English hello→world drag produced
  'hello w'. `sentence.selection_word` (new, pure, tested) ends the span at
  the last hotspot's END; `_selection` uses it.

tests/test_jmdict.py grows selection_word legs (forward/backward/single/
'hello w'); test_word_popup.py gains the single-character preview leg.

### 2026-07-10b — vertical text; the id a card must always know (card_0001)

The first card made after the preview shipped was garbage end to end:
word/sentence 执数单, translations "Number of books" / "number of plates",
no audio. The user's read was "the fallbacks didn't run"; the card's own
provenance says otherwise, and the two real failures were elsewhere.

**The text: a VERTICAL column.** The screenshot shows 抜山蓋世 written
top-to-bottom in a brush font (a game title card); the word box is 286×857.
The rec model reads horizontal lines, so upright it produced 执数单 at conf
0.31 (the card's shaky-OCR note fired correctly). Measured on the REAL crop:

    upright (as shipped)   执验单       0.47
    rotated 90° cw         (nothing)    0.00
    rotated 90° ccw        拔山蓋世      0.99
    per-character cells    拔芸世        mixed

So `ocr.read` now tries a tall box (height ≥ 1.6× width,
`VERTICAL_MIN_ASPECT`) BOTH ways — upright and laid on its side
(`_read_vertical`, 90° ccw; cw reads nothing) — and the better score wins.
Cost: one extra ~20 ms rec pass on tall boxes only; a tall-but-horizontal
box can never LOSE accuracy, since upright still competes. The span maths is
the same midpoint tiling with the axes swapped, so hotspots come back as
cells stacked DOWN the column — click 山 in the column and jmdict answers
'mountain; hill'; drag down it and the selection grows character by
character. Verified on card_0001's real screenshot (0.99) and pinned in
test_ocr_read with a rendered column. Not handled yet: MULTI-column vertical
blocks (columns read right-to-left) — caption_block only stacks horizontal
rows.

**The fallbacks the user asked for already ran** — that half of the card
was correct: no caption track, yet `matched_by: block_life`,
`start_from: onscreen` (6.211 s), `end_from: predicted` (words × pace), and
the loopback rescue cut the 8 s window. The clip died at the SILENCE gate:
peak 1 over the whole window (card_0027's rule — a silent wav is worse than
none). Digital silence means the default output device genuinely carried no
sound: video muted, or audio routed to a non-default device. The bridge was
alive (click_position present, transcript rows seconds earlier), so the
privacy gate was open and the recorder running. Nothing to fix in the
ladder; if this recurs unmuted, instrument the recorder's pause intervals so
the discard note can say WHICH it was.

**The id a card must always know.** The card stamped `video_id: null` while
the transcript file on disk was NAMED by the id — `session.meta()` was empty
until the yt-dlp fetch landed, and this card was made 6 s into a fresh
video. meta() now floors on the session's own id/url and overlays fetched
metadata when it arrives.

### 2026-07-10c — no captions is a note, not a failure (cards 0001/0002)

Three defects under the user's report "audio is not being recorded because
there are no captions" — the belief was wrong (the loopback recorded 21.8 s
on card_0002) but pointed straight at what WAS wrong:

1. **A caption failure silently killed the source-audio download.** Twice:
   `session._load` returned after "no captions" without ever reaching
   `fetch_audio`, and `ensure_audio`'s card-time retry refused when
   `transcript_ready` was false ("nothing to do"). So a caption-less video
   NEVER got its audio downloaded and every card limped on the loopback —
   card_0001's happened to be silent, card_0002's spanned a loop. The clip
   windows come from the SCREEN (2026-07-07); the track only fills edges, so
   caption-less videos are full audio sources now: statuses
   "no captions, audio ready" / "no captions, no audio", the launcher dot
   goes GREEN on audio-only (user call — green = the next card gets good
   audio, track or not), the tip says "clips timed from the screen", and
   each card carries the note "video has no captions — clip timed from the
   on-screen life". The retry machinery keeps working (captions can appear
   once cookies arrive); an explicit `_fetching` flag guards re-entry, since
   the status now says "no captions" WHILE the audio is still downloading.

2. **A 0.6 s window cut 21.8 s of audio** (card_0002). `monotonic_window`
   maps a video window's edges through `bridge.mono_at` INDEPENDENTLY, and
   on a looping Short the same video second plays once per pass — the edges
   landed in different passes, exactly the mono_at failure the queued
   Shorts item predicted. The mapping must preserve duration (with
   playback-rate slack, 0.2×−4×): a stretched or collapsed window is
   refused, and the ladder falls to the OCR-timed loopback cut, which lives
   in ONE clock and clamps at max_clip. The full pass-counter fix stays
   with the Shorts item; this guard just makes the wrong answer impossible.

3. **A perfect read still missed the dictionary.** card_0002's column read
   拔山蓋世 at 0.99 — but 拔 is the OLD form (kyūjitai) of 抜, so JMdict's
   抜山蓋世 entry never matched, the whole idiom fell through to Google and
   came back romanized as a name ('Kokeyama Kaiyo'). jmdict now folds
   old-form kanji to modern (~290-pair table, position-matched strings) as
   a lookup spelling alongside the katakana fold: clicking anywhere in
   拔山蓋世 answers 抜山蓋世【ばつざんがいせい】 'great strength and energy
   (of a mighty hero)'. Brush fonts and game title cards love old forms;
   the surface as written still outranks a folding.

tests/test_session_captionless.py (real SourceSession, stubbed fetchers) +
folding legs in test_jmdict.

### 2026-07-13 — the Shorts loop: video time is not a clock (queued item 5)

Cards 0001/0002 both came off a looping Short; 2026-07-10c's duration-preserving
guard made the WRONG loopback cut impossible, this pass makes the right one
available. Everything in the timing stack assumed video time advances with the
wall clock; a loop breaks that where the queued item predicted, and the fixes
landed in its prescribed order:

- **Bridge samples carry a PASS counter** (`bridge._append_sample`): bumped
  whenever video time jumps backwards — that's exactly when video time stops
  being injective over the clock. A jump landing at ≤ `RESTART_EPS` (1.5 s) is
  also a RESTART (`restart_count`): the loop wrapping, or a seek to the top —
  either way playback starts over and nothing on screen predates second 0.
- **`mono_at` answers from the newest pass that actually played the moment**
  (sampled ct range ± `PASS_SLACK`), so a caption window maps to THIS pass's
  audio, not an occurrence minutes old (card_0002's 0.6 s → 21.8 s stretch).
  A second the newest pass hasn't reached yet falls to the pass that did play
  it — on a loop that is the same audio, and it is actually in the buffer.
  No pass covering it = the old global-nearest answer (single-pass behaviour
  unchanged, e.g. a predicted end slightly ahead of the playhead).
- **`steady_at` forgives the wrap**: jumps before the window's last restart
  no longer poison a stamp, so captions in a pass's first 2 s can anchor
  their own appearance (on a 15 s short that's a meaningful slice of every
  pass). A mid-video seek after the restart still refuses, as ever.
- **A restart force-clears detection**: `wiring.video_restarted()` (consume-
  once) rides the overlay tick into `_refresh_words()` — the same caption,
  same pixels, one loop later never registers a clear, so its `appeared_at`
  kept a stamp from the previous pass; now it's re-accepted fresh.
- **The transcript logs every pass**: `observe(..., pass_id)` from the
  bridge's counter, and the REPEAT_GAP dedupe only swallows a repeat within
  the SAME pass — a short under 10 s used to lose every watch after the
  first to the blip-loop rule.
- The two smaller ones: **clip ends clamp to the video's duration** (clip.py,
  last, after the min/max settings — an end past EOF is empty air on the file
  and next-pass audio on the loopback), and **a session fetch waits for the
  id to hold still 1.5 s** (`VIDEO_ID_DEBOUNCE`) so scrolling the feed stops
  costing a metadata/caption/audio fetch per short flicked past — while
  caption ATTRIBUTION follows the reported id instantly.

Tests: loop legs in test_bridge (pass-aware mono_at, restart-forgiving
steady_at, seek-vs-restart counters), a loop-pass leg in test_ocr_transcript,
a duration-clamp leg in test_youtube_source, and test_source_gate grew the
debounce + consume-once restart tests. Not yet done, per the item's own
advice: grounding on a REAL short — make one card on one looping short and
read its provenance; the overlay-tick wiring itself is live-only territory.

### 2026-07-14 — a restart must WRAP (user report: "so slow to see words")

The Shorts-loop pass shipped a restart detector that was too eager: ANY
backward jump landing near 0 counted, and every "restart" force-clears
detection (hotspots wiped, full rescan). Three things that are not wraps
trip that rule constantly in real browsing — clicking into a DIFFERENT
video (old clock → new clock at 0), an AD resetting the player clock to 0
mid-video (the extension reads currentTime off the <video> element, which
shows AD time under the content's videoId), and two playing <video>
elements alternating in the reports. Each false restart wiped every word
for a rescan; a stream of them is exactly "so slow to see words".

Now `_append_sample` carries the reported videoId and duration, and:

- a VIDEO CHANGE bumps the pass (two videos' clocks are never comparable —
  this also stops mono_at anchoring across videos, a latent flaw) but is
  never a restart;
- a backward jump within one video is a RESTART only when it WRAPS: the
  previous sample sat within END_EPS (2 s) of the reported duration and
  the landing is ≤ RESTART_EPS. Duration unknown (live streams) falls back
  to the landing rule alone;
- the restart flag is stamped ON the sample at append time, and steady_at
  forgives exactly the flagged boundaries — an ad reset or a video change
  stays a seek, as it always was.

Known remaining false positive, judged harmless: a PRE-ROLL ad ends near
its own duration and the content starts at 0 — one spurious restart at the
moment the content begins, when there are no captions worth keeping. An
is-ad signal from the extension would kill it, if it ever matters.

Legs in test_bridge: ad reset / video change bump no restart and earn no
steadiness forgiveness; a real wrap with known duration still restarts.

### 2026-07-16 — run.py boots itself into the venv ("no module named pyside6")

The IDE's Run button launches whatever python the editor happens to have
selected — on this machine a bare interpreter with no PySide6 — and the
launcher died with a traceback (user report). run.py now PROBES its imports:
when they're missing and .venv exists, it re-launches itself with the venv's
python (no activation, no interpreter picking, any python can start it);
no venv prints the pip line instead of a traceback. Verified end-to-end with
the same bare interpreter that failed: it re-execs and the app runs. Also a
local .vscode/settings.json pins the IDE interpreter to the venv — note the
root `settings.json` gitignore pattern catches that file too, so it stays
untracked by design.

### DEFERRED — CC-toggle fakes a caption spawn/clear (card-driven, don't fix yet)

Turning the browser's own captions (CC) on or off makes a block of text
appear/disappear on screen all at once; detection reads it and stamps
"appeared here" / "cleared here" as if a caption naturally popped — which can
feed a wrong clip window. We need to recognise a CC toggle (a whole block
appearing/vanishing at once, not a single caption line cycling) and NOT record
those as honest appear/clear stamps. Left for later, per the user.
