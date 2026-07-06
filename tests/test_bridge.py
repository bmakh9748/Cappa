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
    finally:
        bridge.stop()
    print("ALL PASS")


if __name__ == "__main__":
    main()
