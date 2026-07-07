# Cappa Bridge (browser extension)

Tells the Cappa desktop app which YouTube video you're watching and your exact
playback position. With it installed, Cappa auto-selects the video (no need to
copy the URL) and can time a card's audio by position — which also handles
translated burned-in subtitles that don't text-match the spoken captions.

It sends **only** to `http://127.0.0.1:18765` on your own machine — the running
Cappa app. Nothing leaves your computer.

## Install (Chrome / Edge, one time)

1. Open `chrome://extensions` (or `edge://extensions`).
2. Turn on **Developer mode** (top-right).
3. Click **Load unpacked** and pick this `extension/` folder.
4. Keep it enabled. Open a YouTube video; if Cappa is running, its launcher
   tooltip shows `yt: ...` and the video is selected automatically.

## How it works

- `content.js` runs on youtube.com and snapshots `{videoId, currentTime,
  paused, title}` ~once a second.
- `background.js` (the service worker) POSTs each snapshot to the app's local
  bridge. Only the worker can reach localhost, so the content script hands off
  to it via `chrome.runtime.sendMessage`.
- The app ([cappa/source/bridge.py](../cappa/source/bridge.py)) keeps the latest
  and extrapolates position between updates.

If the port `18765` is ever busy, change it in `background.js`,
`manifest.json` and `cappa/source/bridge.py`. (It was moved off `8765`
because AnkiConnect uses that one, and an Anki running next to Cappa
swallowed the extension's reports.)

**After changing any extension file** (including this port move): open
`chrome://extensions` and hit the reload arrow on Cappa Bridge, then reload
the YouTube tab. Edited files do nothing until then.
