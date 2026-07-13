"""source_wiring gates: live_video_id (on-screen captions are attributed to
a video ONLY while the extension reports it live and playing), the video-id
debounce (a Shorts scroll must not fire a fetch per short flicked past) and
the consume-once restart signal (a looping Short wrapping to 0 force-clears
detection exactly once per wrap)."""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cappa.ui import source_wiring as sw
from cappa.ui.source_wiring import live_video_id


def test_live_video_id():
    vid = "abc123"
    # Fresh, playing report -> the video id.
    assert live_video_id({"videoId": vid, "paused": False}, vid) == vid
    # Stale: bridge.current() returns None once reports stop (tab hidden/
    # closed, or a non-YouTube tab). Whatever is on screen is NOT this video.
    assert live_video_id(None, vid) is None
    # Paused: a frozen frame is not caption life.
    assert live_video_id({"videoId": vid, "paused": True}, vid) is None
    # A report with no paused key is treated as playing (older payloads).
    assert live_video_id({"videoId": vid}, vid) == vid
    # No id known yet, even with a fresh report.
    assert live_video_id({"paused": False}, None) is None
    print("PASS source gate: captions attach to a video only while it "
          "plays live")


def _bare_wiring():
    """A SourceWiring with just the state _poll_browser touches — no
    recorder device, no bridge server, no session threads."""
    w = sw.SourceWiring.__new__(sw.SourceWiring)
    w._audio_off = False
    w._bridge_ever = False
    w._bridge_lost_at = None
    w._recorder_paused = False
    w.recorder = types.SimpleNamespace(start=lambda: None,
                                       stop=lambda: None, error="")
    w._bridge_video_id = None
    w._session_video_id = None
    w._vid_since = 0.0
    w._restarts_seen = 0
    w._ext_version = ""
    w._caption_retry = (None, 0, 0.0)
    return w


def test_session_fetch_waits_for_a_stable_id():
    """Scrolling the Shorts feed rewrites the reported video id every
    ~700 ms report, and each rewrite used to fire a session fetch —
    metadata, captions and an audio download PER SHORT flicked past. The
    fetch now waits for the id to hold still (VIDEO_ID_DEBOUNCE), while
    caption ATTRIBUTION follows the reported id instantly."""
    orig_prefs = sw.card_prefs
    sw.card_prefs = types.SimpleNamespace(include=lambda k: True)
    try:
        fetched = []
        w = _bare_wiring()
        w.session = types.SimpleNamespace(
            set_video=lambda url: fetched.append(url),
            fetching=False, status="idle")
        state = {"videoId": "shortA", "url": "uA", "paused": False}
        w.bridge = types.SimpleNamespace(current=lambda: state)

        w._poll_browser()
        assert w._bridge_video_id == "shortA", "attribution must be instant"
        assert fetched == [], "the fetch must wait out the debounce"
        w._poll_browser()
        assert fetched == []
        # Scrolled on before it settled: shortA never costs a fetch.
        state = {"videoId": "shortB", "url": "uB", "paused": False}
        w._poll_browser()
        assert w._bridge_video_id == "shortB" and fetched == []
        # shortB holds still past the debounce (rewound, not slept).
        w._vid_since -= sw.VIDEO_ID_DEBOUNCE
        w._poll_browser()
        assert fetched == ["uB"], fetched
        assert w._session_video_id == "shortB"
        # Steady reports afterwards never re-fetch.
        w._poll_browser()
        assert fetched == ["uB"], fetched
    finally:
        sw.card_prefs = orig_prefs
    print("PASS source gate: only an id stable for the debounce earns a "
          "fetch; attribution follows the screen instantly")


def test_video_restarted_consumes_once():
    """One overlay refresh per observed wrap: the signal reads the bridge's
    restart_count and reports each change exactly once (several wraps
    between ticks still mean one stale-memory clear)."""
    w = _bare_wiring()
    w.bridge = types.SimpleNamespace(restart_count=0)
    assert w.video_restarted() is False
    w.bridge.restart_count = 1
    assert w.video_restarted() is True
    assert w.video_restarted() is False
    w.bridge.restart_count = 3
    assert w.video_restarted() is True
    assert w.video_restarted() is False
    print("PASS source gate: a restart clears detection once per wrap")


if __name__ == "__main__":
    test_live_video_id()
    test_session_fetch_waits_for_a_stable_id()
    test_video_restarted_consumes_once()
    print("ALL PASS")
