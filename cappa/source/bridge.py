"""Localhost bridge: hears which YouTube video the browser is playing.

The Cappa browser extension POSTs the active video's {videoId, currentTime,
paused, ...} here about once a second. The latest is kept (stamped with our
monotonic clock, so position extrapolates between updates and stale state is
ignored), plus a short HISTORY of samples: `mono_at(video_t)` maps a video
timestamp to the monotonic moment it actually played through the speakers --
which lets the flashcard cut caption-exact audio out of the loopback ring
buffer when the source download isn't available. The extension also POSTs the
user's youtube.com cookies to /cookies; they're written to a Netscape-format
file for yt-dlp, whose fetches otherwise trip YouTube's anonymous bot check.

A tiny threaded http.server on 127.0.0.1. Fail-soft in the app's spirit: if the
port is taken or serving dies, the bridge stays down with an `error` and the app
runs exactly as before (the manual 'Use video from clipboard' path still works).
No Qt."""

import json
import os
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "127.0.0.1"
PORT = 8765
STALE_AFTER = 5.0    # no update within this many seconds -> position unknown
HISTORY = 200        # ~2.3 min of samples at the extension's ~700ms cadence
MAX_ANCHOR_GAP = 90.0  # a mapping farther than the loopback buffer is useless


class BrowserBridge:
    def __init__(self, host=HOST, port=PORT):
        self._host = host
        self._port = port
        self._lock = threading.Lock()
        self._state = None       # latest payload dict from the extension
        self._at = 0.0           # monotonic time it arrived
        # (mono_arrival, currentTime, paused) samples for mono_at(): playback
        # history survives pauses and seeks because each sample is its own
        # video-time <-> clock-time anchor.
        self._history = deque(maxlen=HISTORY)
        self._server = None
        self._thread = None
        self.port = port         # actual bound port (differs if port=0)
        self.error = ""
        self.cookies_at = 0.0    # monotonic time cookies last arrived

    # ------------------------------------------------------------ lifecycle
    def start(self):
        """Bind and serve on a daemon thread. Idempotent. On failure sets
        `error` and stays down."""
        if self._server is not None:
            return
        try:
            self._server = ThreadingHTTPServer(
                (self._host, self._port), self._make_handler())
        except OSError as exc:
            self.error = "bridge could not bind %s:%d (%s)" % (
                self._host, self._port, exc)
            return
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        srv = self._server
        self._server = None
        if srv is not None:
            try:
                srv.shutdown()
                srv.server_close()
            except Exception:
                pass

    # --------------------------------------------------------------- state
    def _update(self, data):
        now = time.monotonic()
        with self._lock:
            self._state = data
            self._at = now
            ct = data.get("currentTime")
            if isinstance(ct, (int, float)):
                self._history.append((now, float(ct),
                                      bool(data.get("paused", False))))

    def current(self):
        """Latest browser state, or None if nothing fresh. Adds `age` and a
        `play_time` extrapolated forward while playing (frozen when paused)."""
        with self._lock:
            state, at = self._state, self._at
        if state is None:
            return None
        age = time.monotonic() - at
        if age > STALE_AFTER:
            return None
        out = dict(state)
        out["age"] = age
        ct = state.get("currentTime")
        if ct is None:
            out["play_time"] = None
        elif state.get("paused", False):
            out["play_time"] = ct
        else:
            out["play_time"] = ct + age
        return out

    def mono_at(self, video_t):
        """The monotonic moment video time `video_t` played through the
        speakers, or None. Uses the nearest playing sample as the anchor:
        mono = anchor_mono + (video_t - anchor_ct). Sample-by-sample anchoring
        keeps the mapping honest across pauses and seeks -- an anchor on the
        same playing stretch maps exactly; the nearest one otherwise is off by
        at most the gap to it."""
        if video_t is None:
            return None
        with self._lock:
            samples = list(self._history)
        best = None  # (|ct - video_t|, mono, ct)
        for mono, ct, paused in samples:
            if paused:
                continue
            d = abs(ct - video_t)
            if best is None or d < best[0]:
                best = (d, mono, ct)
        if best is None or best[0] > MAX_ANCHOR_GAP:
            return None
        _, mono, ct = best
        return mono + (video_t - ct)

    # ------------------------------------------------------------- cookies
    def _write_cookies(self, cookies):
        """Write extension-supplied cookie dicts as a Netscape cookies.txt for
        yt-dlp (chrome.cookies.getAll shape: domain/path/secure/expirationDate/
        name/value). Returns how many were written."""
        from .youtube import cookie_file_path
        lines = ["# Netscape HTTP Cookie File", ""]
        n = 0
        for c in cookies:
            try:
                domain = c["domain"]
                include_sub = "TRUE" if domain.startswith(".") else "FALSE"
                secure = "TRUE" if c.get("secure") else "FALSE"
                expiry = str(int(c.get("expirationDate") or 0))
                lines.append("\t".join([domain, include_sub,
                                        c.get("path") or "/", secure, expiry,
                                        c["name"], c.get("value") or ""]))
                n += 1
            except (KeyError, TypeError, ValueError):
                continue
        if not n:
            return 0
        path = cookie_file_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
        self.cookies_at = time.monotonic()
        return n

    # ------------------------------------------------------------- handler
    def _make_handler(bridge_self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # keep the console quiet

            def _cors(self):
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                # Chrome Private Network Access preflight for loopback targets.
                self.send_header("Access-Control-Allow-Private-Network", "true")

            def do_OPTIONS(self):
                self.send_response(204)
                self._cors()
                self.end_headers()

            def do_GET(self):
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok":true,"app":"cappa"}')

            def do_POST(self):
                try:
                    n = int(self.headers.get("Content-Length", "0"))
                    data = json.loads(self.rfile.read(n) or b"{}")
                except Exception:
                    data = None
                if isinstance(data, dict):
                    if self.path == "/cookies":
                        try:
                            bridge_self._write_cookies(
                                data.get("cookies") or [])
                        except Exception:
                            pass  # cookies are an optimization, never fatal
                    else:
                        bridge_self._update(data)
                self.send_response(204)
                self._cors()
                self.end_headers()

        return Handler
