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
    # Through _append_sample, so samples carry honest pass counters exactly
    # as live extension reports would.
    with bridge._lock:
        for mono, ct, paused in samples:
            bridge._append_sample(mono, ct, paused)


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


def test_mono_at_resolves_in_the_newest_pass():
    """A looping Short plays the same video second once per PASS, so the
    nearest sample overall can sit a playthrough ago — card_0002 cut 21.8 s
    of audio because the window's edges landed in different passes. mono_at
    answers from the newest pass that actually played video_t."""
    b = BrowserBridge(port=0)
    # Pass 0: a full 30 s playthrough. Pass 1: wrapped, 2.2 s in so far.
    _fill(b, [(100.0 + k, 0.5 + k, False) for k in range(30)])
    _fill(b, [(130.0, 0.2, False), (131.0, 1.2, False), (132.0, 2.2, False)])
    assert b.pass_count() == 1, b.pass_count()
    assert b.restart_count == 1, b.restart_count
    # video second 0.7 exists in BOTH passes; the pass-0 sample at ct 0.5
    # is the nearest overall (d 0.2, thirty seconds of clock ago) — but the
    # newest pass played 0.7 too, and that occurrence is the answer.
    m = b.mono_at(0.7)
    assert m is not None and m > 129.0, m
    assert abs(m - 130.5) < 1e-6, m
    # video second 15 hasn't played yet in pass 1: the pass that DID play
    # it answers — never a future extrapolation from the newest pass.
    m15 = b.mono_at(15.0)
    assert abs(m15 - 114.5) < 1e-6, m15
    # A mid-video backward seek starts a new pass but is NOT a restart.
    _fill(b, [(133.0 + k, 3.2 + k, False) for k in range(17)])  # play on
    assert b.pass_count() == 1, b.pass_count()   # forward play: same pass
    _fill(b, [(151.0, 10.0, False)])             # drag back to 10 s
    assert b.pass_count() == 2, b.pass_count()
    assert b.restart_count == 1, b.restart_count
    print("PASS bridge: mono_at answers from the newest pass that played "
          "the moment; a mid-video seek is no restart")


def test_steady_at_forgives_a_restart():
    """A loop wrap (or a seek to 0) is not a seek: playback starts over and
    nothing on screen predates second 0, so a caption popping in a pass's
    first seconds anchors its own appearance. A mid-video jump after the
    restart still poisons the stamp."""
    b = BrowserBridge(port=0)
    _fill(b, [(100.0, 28.6, False), (100.7, 29.3, False),
              (101.4, 0.3, False), (102.1, 1.0, False), (102.8, 1.7, False)])
    assert b.steady_at(102.5) is True, "a restart must not poison the stamp"
    # A jump AFTER the restart, inside the window: still a seek.
    b2 = BrowserBridge(port=0)
    _fill(b2, [(300.0, 29.5, False), (300.7, 0.4, False),
               (301.4, 8.0, False), (302.1, 8.7, False)])
    assert b2.steady_at(301.8) is False, "a seek after the restart poisons"
    print("PASS bridge: steady_at forgives the loop wrap, still refuses "
          "seeks after it")


def test_ad_reset_and_video_change_are_not_restarts():
    """An AD resets the player clock to 0 mid-video, and a video change
    starts a new clock at 0 — neither is the video WRAPPING, yet each used
    to count as a restart, and every false restart force-cleared detection
    (user report, 2026-07-14: 'so slow to see words'). Both still bump the
    PASS (their clocks are not comparable); neither bumps the restart
    count nor earns steady_at's forgiveness."""
    b = BrowserBridge(port=0)
    with b._lock:
        # Watching a 600 s video, 300 s in...
        for k in range(4):
            b._append_sample(100.0 + 0.7 * k, 300.0 + 0.7 * k, False,
                             "vidA", 600.0)
        # ...a mid-roll ad takes the player: the clock resets to 0.
        for k in range(3):
            b._append_sample(102.8 + 0.7 * k, 0.1 + 0.7 * k, False,
                             "vidA", 15.0)
    assert b.pass_count() == 1, b.pass_count()
    assert b.restart_count == 0, b.restart_count
    # The reset is still a JUMP: a stamp right after it is not steady.
    assert b.steady_at(103.2) is False
    # Clicking into a different video: new pass, still no restart.
    with b._lock:
        b._append_sample(105.0, 0.4, False, "vidB", 240.0)
    assert b.pass_count() == 2, b.pass_count()
    assert b.restart_count == 0, b.restart_count
    # A genuine wrap with the duration known IS still a restart.
    b2 = BrowserBridge(port=0)
    with b2._lock:
        b2._append_sample(200.0, 14.1, False, "vidS", 15.0)
        b2._append_sample(200.7, 14.8, False, "vidS", 15.0)
        b2._append_sample(201.4, 0.3, False, "vidS", 15.0)
    assert b2.restart_count == 1, b2.restart_count
    print("PASS bridge: ad resets and video changes never count as "
          "restarts; a real wrap still does")


if __name__ == "__main__":
    main()
    test_video_at_honest()
    test_steady_at()
    test_mono_at_resolves_in_the_newest_pass()
    test_steady_at_forgives_a_restart()
    test_ad_reset_and_video_change_are_not_restarts()
