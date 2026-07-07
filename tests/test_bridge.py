"""Unit test: the localhost browser bridge accepts POSTed state and serves it
back with an extrapolated playback position. Binds an ephemeral port (port=0)
so it never collides with a running app or another test. Network-free beyond
loopback; windowless."""

import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cappa.source.bridge import BrowserBridge


def _post(port, payload):
    req = urllib.request.Request(
        "http://127.0.0.1:%d/state" % port,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=3) as r:
        return r.status


def _get(port):
    with urllib.request.urlopen(
            "http://127.0.0.1:%d/health" % port, timeout=3) as r:
        return r.status, r.read()


def main():
    bridge = BrowserBridge(port=0)
    bridge.start()
    assert not bridge.error, bridge.error
    port = bridge.port
    try:
        assert bridge.current() is None, "empty bridge should report no state"

        code = _post(port, {"videoId": "abcdefghijk", "url": "u",
                            "currentTime": 12.0, "paused": True})
        assert code == 204, code
        st = bridge.current()
        assert st and st["videoId"] == "abcdefghijk", st
        assert abs(st["play_time"] - 12.0) < 0.2, st["play_time"]  # paused: frozen
        print("PASS bridge: POST stored; paused play_time frozen at 12.0")

        _post(port, {"videoId": "abcdefghijk", "currentTime": 30.0,
                     "paused": False})
        time.sleep(0.3)
        st = bridge.current()
        assert 30.0 <= st["play_time"] < 31.0, st["play_time"]  # playing: drifts
        print("PASS bridge: playing play_time extrapolates forward (%.2f)"
              % st["play_time"])

        code, body = _get(port)
        assert code == 200 and b"cappa" in body, (code, body)
        print("PASS bridge: GET health ok")

        # mono_at: video time maps back through the nearest playing sample.
        # The playing sample above anchored currentTime=30.0 at its arrival;
        # video time 28.5 played 1.5s before that arrival.
        before = time.monotonic()
        m = bridge.mono_at(28.5)
        assert m is not None, "mono_at returned None"
        assert m < before, "28.5s played in the past, not the future"
        m30 = bridge.mono_at(30.0)
        assert abs((m30 - m) - 1.5) < 0.05, (m, m30)
        assert bridge.mono_at(30.0 + 500.0) is None, "beyond buffer must be None"
        print("PASS bridge: mono_at maps video time onto the clock")

        # /cookies writes a Netscape file for yt-dlp (path patched to a temp).
        import tempfile
        from cappa.source import youtube as yt
        with tempfile.TemporaryDirectory() as d:
            cookie_path = os.path.join(d, "cookies.txt")
            orig = yt.cookie_file_path
            yt.cookie_file_path = lambda cache_dir=None: cookie_path
            try:
                req = urllib.request.Request(
                    "http://127.0.0.1:%d/cookies" % port,
                    data=json.dumps({"cookies": [
                        {"domain": ".youtube.com", "path": "/", "secure": True,
                         "expirationDate": 1900000000.5, "name": "SID",
                         "value": "abc123"},
                        {"bogus": True},
                    ]}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST")
                with urllib.request.urlopen(req, timeout=3) as r:
                    assert r.status == 204, r.status
                with open(cookie_path, encoding="utf-8") as f:
                    text = f.read()
            finally:
                yt.cookie_file_path = orig
        assert text.startswith("# Netscape HTTP Cookie File"), text[:40]
        line = [l for l in text.splitlines() if l.startswith(".youtube.com")][0]
        assert line.split("\t") == [".youtube.com", "TRUE", "/", "TRUE",
                                    "1900000000", "SID", "abc123"], line
        print("PASS bridge: /cookies writes a valid Netscape cookie file")

        # A port someone else holds must FAIL LOUDLY, never bind alongside.
        # Windows happily lets two reuse-flagged sockets share a port and
        # splits the traffic between apps: AnkiConnect on 8765 silently ate
        # the extension's reports while Cappa reported "yt: idle".
        other = BrowserBridge(port=port)
        other.start()
        assert other.error, "second bind on a taken port must set error"
        assert "could not bind" in other.error, other.error
        other.stop()
        # The original bridge is unharmed and still answering.
        code, body = _get(port)
        assert code == 200 and b"cappa" in body, (code, body)
        print("PASS bridge: a taken port errors instead of silently sharing")
    finally:
        bridge.stop()
    print("ALL PASS")


def _fill(bridge, samples):
    for s in samples:
        bridge._history.append(s)


def test_video_at_honest():
    """video_at anchors on the BRACKETING samples and never extrapolates
    through a pause or a seek (card_0005: a stamp 0.8s into the pause
    mapped 1.1s past where the video sat; card_0007: 34s off through a
    paused seek)."""
    b = BrowserBridge(port=0)   # never started: history fed directly
    _fill(b, [(100.0, 50.0, False), (100.7, 50.7, False),
              (101.4, 51.0, True), (102.1, 51.0, True),
              (110.0, 51.0, True)])
    # Inside the playing stretch: interpolates.
    assert abs(b.video_at(100.35) - 50.35) < 1e-6
    # Straddling play -> pause (video froze at 51.0): advances but clamps
    # at the frozen position instead of extrapolating past it.
    assert abs(b.video_at(101.2) - 51.0) < 1e-6, b.video_at(101.2)
    # Deep inside the pause: frozen, no matter how much clock passes.
    assert abs(b.video_at(105.0) - 51.0) < 1e-6, b.video_at(105.0)
    # A seek between playing samples: that moment was never reported.
    b2 = BrowserBridge(port=0)
    _fill(b2, [(100.0, 50.0, False), (100.7, 90.0, False)])
    assert b2.video_at(100.35) is None, b2.video_at(100.35)
    print("PASS bridge: video_at clamps at pauses and refuses hidden seeks")


def test_steady_at():
    """steady_at: did the video REACH this moment without a jump? The
    appearance anchor's witness (cards 2-9, 2026-07-07)."""
    play = [(100.0 + 0.7 * k, 50.0 + 0.7 * k, False) for k in range(8)]
    b = BrowserBridge(port=0)
    _fill(b, play)
    assert b.steady_at(102.0) is True
    # A pause beginning right after the stamp is FINE (pausing to click is
    # the card-making motion), and a stamp that lags slightly into the
    # pause still counts — play was seen moments before (card_0005).
    b2 = BrowserBridge(port=0)
    _fill(b2, [(100.0, 50.0, False), (100.7, 50.7, False),
               (101.4, 51.0, True), (102.1, 51.0, True)])
    assert b2.steady_at(101.2) is True, "straddle must stay trusted"
    # Deep inside the pause: the row popped on a frozen frame.
    assert b2.steady_at(104.0) is False
    # A seek between samples poisons the stamp (the landing row was
    # already mid-life).
    b3 = BrowserBridge(port=0)
    _fill(b3, [(100.0, 50.0, False), (100.7, 50.7, False),
               (101.4, 90.0, False), (102.1, 90.7, False)])
    assert b3.steady_at(101.8) is False
    # ...but once playback has run steadily past it, later rows are fine.
    _fill(b3, [(102.8 + 0.7 * k, 91.4 + 0.7 * k, False) for k in range(4)])
    assert b3.steady_at(105.0) is True
    # Silence: nothing can vouch either way.
    b4 = BrowserBridge(port=0)
    assert b4.steady_at(100.0) is None
    print("PASS bridge: steady_at trusts play, refuses seeks and dead "
          "pauses, shrugs at silence")


if __name__ == "__main__":
    main()
    test_video_at_honest()
    test_steady_at()
