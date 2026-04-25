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
| OCR | **PaddleOCR** | Fast, accurate, designed for real-time, runs locally |
| Overlay | **pywin32 / win32gui** | Transparent always-on-top window on Windows |
| Audio extraction | **ffmpeg** via `ffmpeg-python` | Slice audio clips by timestamp |
| Translation | **Claude API** (`claude-sonnet-4-20250514`) | Translation + definition on word click only (not real-time) |
| Flashcard export | **genanki** | Generates Anki `.apkg` files with embedded media |
| OS | **Windows only** (for now) | Overlay behaviour uses Win32 APIs |
| Language | **Python 3.11+** | |

### Install commands
```bash
pip install PySide6 qt-material mss numpy paddlepaddle paddleocr pywin32 ffmpeg-python genanki anthropic
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

## Current Status

> Planning phase complete. No code written yet.
> Next step: build the overlay window (#1) — a transparent always-on-top PySide6 window that sits over the browser and proves the basic windowing model works on Windows.
