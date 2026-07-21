"""Unit test: the pronunciation path (cappa.pronounce). Network-free and
silent: the TTS fetch is monkeypatched with canned MP3-ish bytes and the
winmm player with a recorder — what's tested is the contract around them
(cache, temp-file lifecycle, displayable failures)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cappa.pronounce as P
from cappa import winapi


def main():
    orig_fetch = P._fetch
    orig_play = winapi.play_mp3_blocking
    fetches, plays = [], []

    def fake_fetch(text, lang):
        fetches.append((text, lang))
        return b"\xff\xf3FAKEMP3"

    def fake_play(path):
        # The file must exist WITH the audio bytes at play time.
        with open(path, "rb") as f:
            assert f.read() == b"\xff\xf3FAKEMP3"
        plays.append(path)

    try:
        P._cache.clear()
        P._fetch = fake_fetch
        winapi.play_mp3_blocking = fake_play

        # say(): fetch, write, play, clean up.
        P.say("makan", "id")
        assert fetches == [("makan", "id")] and len(plays) == 1
        assert plays[0].endswith(".mp3")
        assert not os.path.exists(plays[0]), "temp mp3 left behind"
        print("PASS pronounce: say fetches, plays, and removes the temp file")

        # The byte cache makes the re-click free (and offline).
        P.say("makan", "id")
        assert len(fetches) == 1 and len(plays) == 2
        # A different language is a different voice: a real fetch again.
        P.say("makan", "ms")
        assert len(fetches) == 2
        print("PASS pronounce: repeat words replay from the cache")

        # Junk input fails with a displayable reason, not a request.
        for bad in ("", "   ", "x" * (P.MAX_CHARS + 1)):
            try:
                P.fetch(bad, "id")
                raise AssertionError("expected PronounceError for %r" % bad)
            except P.PronounceError:
                pass
        assert len(fetches) == 2, "junk input still hit the endpoint"
        print("PASS pronounce: empty/oversized text never reaches the "
              "endpoint")

        # A word the popup already moved past is fetched (cached for the
        # re-click) but never played.
        plays.clear()
        P.say("makan", "id", still_wanted=lambda: False)
        assert plays == [], "audio played for a word the popup left behind"
        print("PASS pronounce: stale requests stay silent")

        # A playback failure surfaces as PronounceError and still cleans up.
        def broken_play(path):
            plays.append(path)
            raise OSError("MCI: device error")

        winapi.play_mp3_blocking = broken_play
        try:
            P.say("makan", "id")
            raise AssertionError("expected PronounceError")
        except P.PronounceError as exc:
            assert "playback" in str(exc)
        assert not os.path.exists(plays[-1]), "temp mp3 leaked on failure"
        print("PASS pronounce: playback failure is displayable, no file leak")
    finally:
        P._fetch = orig_fetch
        winapi.play_mp3_blocking = orig_play
        P._cache.clear()
    print("ALL PASS")


if __name__ == "__main__":
    main()
