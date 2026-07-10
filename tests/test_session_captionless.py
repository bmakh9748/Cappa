"""A caption-less video still downloads its audio (cards 0001/0002).

The session's _load used to RETURN on a caption failure, so a video with no
caption track never got its audio downloaded and every card limped on the
loopback buffer. These tests drive the REAL SourceSession with the youtube
fetchers stubbed at the module seam: captions fail, audio must still land,
the status must say so, and ensure_audio's card-time retry must not refuse
just because there's no transcript.

Also pins monotonic_window's duration guard: on a LOOPING video the same
video second plays once per pass, and mapping a window's edges independently
let a 0.6 s window come back as 21.8 s of wall clock (card_0002's audio
spanned the loop). A mapping that doesn't preserve duration is refused.

No network, no Qt; the fetch thread is real, waited on with a timeout.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cappa.source import session as session_mod
from cappa.source import youtube
from cappa.source.session import SourceSession
from cappa.source.youtube import SourceError

URL = "https://www.youtube.com/watch?v=stubvideo01"


def wait_for(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return False


class Stub:
    """Counted stand-ins for the youtube fetchers."""

    def __init__(self, captions_fail=True, audio_fail=False):
        self.captions_fail = captions_fail
        self.audio_fail = audio_fail
        self.caption_calls = 0
        self.audio_calls = 0

    def fetch_transcript(self, url, lang=None):
        self.caption_calls += 1
        if self.captions_fail:
            raise SourceError("no captions found")
        raise AssertionError("not used in these tests")

    def fetch_audio(self, url):
        self.audio_calls += 1
        if self.audio_fail:
            raise SourceError("download failed")
        return "C:/fake/cache/stubvideo01.webm"


def with_stub(stub):
    session_mod.youtube.fetch_transcript = stub.fetch_transcript
    session_mod.youtube.fetch_audio = stub.fetch_audio


real_transcript, real_audio = youtube.fetch_transcript, youtube.fetch_audio
try:
    # ---- captions fail -> the audio download STILL runs ------------------
    stub = Stub(captions_fail=True)
    with_stub(stub)
    s = SourceSession()
    s.set_video(URL)
    assert wait_for(lambda: s.audio_ready), (s.status, s.error)
    assert not s.transcript_ready
    assert s.status == "no captions, audio ready", s.status
    assert s.audio_path == "C:/fake/cache/stubvideo01.webm"
    assert stub.audio_calls == 1
    print("PASS: no captions still downloads the audio; status says both")

    # ---- a retry mid-flight must not start a second download -------------
    # (the fetch thread is guarded by _fetching, not by the status string,
    # because the status says "no captions" WHILE the audio downloads)
    assert not s.fetching
    s.set_video(URL)          # same video, nothing in flight -> full refetch
    assert wait_for(lambda: s.audio_ready and not s.fetching)
    assert stub.audio_calls == 2, stub.audio_calls
    print("PASS: re-pointing at the video refetches once, not concurrently")

    # ---- both fail -> honest status; ensure_audio retries without captions
    stub = Stub(captions_fail=True, audio_fail=True)
    with_stub(stub)
    s = SourceSession()
    s.set_video(URL)
    assert wait_for(lambda: not s.fetching and s.status != "loading captions")
    assert s.status == "no captions, no audio", s.status
    assert not s.audio_ready
    # the card-time retry used to bail with "no transcript: nothing to do"
    stub.audio_fail = False
    path = s.ensure_audio(timeout=1.0)
    assert path == "C:/fake/cache/stubvideo01.webm", path
    assert s.audio_ready and s.status == "no captions, audio ready"
    print("PASS: ensure_audio retries a caption-less video's audio")

    # ---- monotonic_window refuses a duration-warping mapping -------------
    s = SourceSession()
    s.set_mono_mapper(lambda t: 111959.4 if t < 6.0 else 111981.2)
    assert s.monotonic_window(5.456, 6.056) is None, \
        "a 0.6s window mapped to 21.8s must be refused (loop straddle)"
    s.set_mono_mapper(lambda t: 111959.4 + (t - 5.456))
    got = s.monotonic_window(5.456, 6.056)
    assert got is not None and abs((got[1] - got[0]) - 0.6) < 1e-6
    print("PASS: monotonic_window preserves duration or refuses")
finally:
    session_mod.youtube.fetch_transcript = real_transcript
    session_mod.youtube.fetch_audio = real_audio

print("\nALL PASS")
