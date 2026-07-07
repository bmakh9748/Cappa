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
import socket
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "127.0.0.1"
# NOT 8765: that is AnkiConnect's port, and Anki is exactly the app a Cappa
# user has open. With both bound (Windows lets two sockets share a port when
# the reuse flag is set), the extension's reports were delivered to Anki and
# Cappa sat on "yt: idle" -- whichever app launched first won, which is why
# the failure came and went with reboots. Must match extension/background.js
# and extension/manifest.json.
PORT = 18765
STALE_AFTER = 5.0    # no update within this many seconds -> position unknown
HISTORY = 200        # ~2.3 min of samples at the extension's ~700ms cadence
MAX_ANCHOR_GAP = 90.0  # a mapping farther than the loopback buffer is useless

# steady_at(): how much playback around a moment must be seen continuous for
# a caption's appearance stamp to mean "the sentence starts here".
STEADY_BEFORE = 2.0  # this far BACK — a seek landing takes detection under
                     # ~1s to notice, so the jump sits within this window
STEADY_GAP = 4.0     # nearest samples farther than this can't vouch (the
                     # extension posts ~700ms apart; a hole this big means
                     # the tab was hidden or reports stopped)
SEEK_SLACK = 0.75    # |video-time step − clock step| beyond this is a seek,
                     # not report jitter (real seeks jump by seconds)
STRADDLE_MAX = 1.5   # a stamp may trail the last playing sample by this much
                     # and still count as "appeared during play": detection
                     # lag plus one report gap lands appear stamps just past
                     # the pause line (card_0005 stamped 0.8s into the pause
                     # for a row that plainly popped before it)


class _ExclusiveHTTPServer(ThreadingHTTPServer):
    """Never share the port. http.server's default reuse flag is what let
    the bridge and AnkiConnect both hold 8765 without an error; exclusive
    binding turns a taken port into an OSError that start() surfaces."""
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):   # Windows
            self.socket.setsockopt(socket.SOL_SOCKET,
                                   socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


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
            self._server = _ExclusiveHTTPServer(
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

    def age(self):
        """Seconds since the extension last reported, or None if it never
        has (the clipboard-only path). For UI staleness checks that want a
        TIGHTER window than current()'s STALE_AFTER."""
        with self._lock:
            if self._state is None:
                return None
            at = self._at
        return time.monotonic() - at

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

    def video_at(self, mono):
        """The video time SHOWING at monotonic moment `mono`, or None —
        anchored on the samples BRACKETING the moment, never extrapolated
        through a pause or a seek. The old nearest-playing-sample anchoring
        assumed playback kept running: a caption stamp landing 0.8s into a
        pause mapped 1.1s PAST where the video actually sat (card_0005),
        and one landing near a paused seek mapped 34s into the abandoned
        stretch (card_0007). A paused stretch maps to its frozen position;
        a seek hidden between the brackets returns None — that moment's
        true position was never reported."""
        if mono is None:
            return None
        with self._lock:
            samples = list(self._history)
        before = after = None
        for s in samples:
            if s[0] <= mono:
                before = s
            else:
                after = s
                break
        if before is None and after is None:
            return None
        if before is None:
            m, ct, paused = after
            if m - mono > MAX_ANCHOR_GAP:
                return None
            return ct if paused else max(0.0, ct - (m - mono))
        if after is None:
            m, ct, paused = before
            if mono - m > MAX_ANCHOR_GAP:
                return None
            return ct if paused else ct + (mono - m)
        (bm, bct, bp), (am, act, ap) = before, after
        if bp and ap:
            return bct       # paused stretch: the screen held bct (a seek
                             # while paused only shows from `after` onward)
        if bp:               # pause -> play: resumed somewhere in between
            return max(bct, act - (am - mono))
        est = bct + (mono - bm)
        if ap:               # play -> pause: video froze somewhere between
            if act < bct - SEEK_SLACK or act > bct + (am - bm) + SEEK_SLACK:
                return None  # ...and a seek hid in the same gap
            return min(est, act)
        if abs((act - bct) - (am - bm)) > SEEK_SLACK:
            return None      # seek between the brackets: never reported
        return est

    def steady_at(self, mono):
        """Did the video REACH monotonic moment `mono` without a jump — no
        seek, no popping in deep inside a pause? True/False, or None when
        the history can't say (no extension, reports too sparse). The
        flashcard's appearance anchor asks this before trusting a stamp:
        a row seen appearing during normal playback carries its sentence's
        start (a pause beginning moments later is fine — pausing to click
        IS the card-making motion, and pausing never skips video time);
        a row that appeared because of a seek, or while long paused, was
        already mid-life and its true start was never on screen (user rule;
        every wrong clip of cards 2-9, 2026-07-07, traced to a trusted
        landing stamp)."""
        if mono is None:
            return None
        with self._lock:
            samples = list(self._history)
        before = [s for s in samples if s[0] <= mono]
        if not before:
            return None
        if mono - before[-1][0] > STEADY_GAP:
            return None                    # reports hole right at the stamp
        last_play = next((s for s in reversed(before) if not s[2]), None)
        if last_play is None or mono - last_play[0] > STRADDLE_MAX:
            # Never seen playing near the stamp: the row popped deep inside
            # a pause (a paused seek, a re-rendered frame) — that is not a
            # real appearance, its sentence was never heard starting.
            return False
        after = [s for s in samples if s[0] > mono]
        lo = mono - STEADY_BEFORE
        window = [s for s in samples if lo <= s[0] <= mono]
        prev = [s for s in samples if s[0] < lo]
        if prev:
            window.insert(0, prev[-1])
        if after:
            window.append(after[0])
        for (m1, c1, p1), (m2, c2, p2) in zip(window, window[1:]):
            dm, dc = m2 - m1, c2 - c1
            if p1 or p2:
                # Across a pause, video time may hold still or advance up
                # to the elapsed clock — anything else is a seek.
                if dc < -SEEK_SLACK or dc > dm + SEEK_SLACK:
                    return False
            elif abs(dc - dm) > SEEK_SLACK:
                return False
        return True

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
