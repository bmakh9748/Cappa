"""Cappa's own transcript of what it WATCHED: every caption row the tracker
confirmed, written down with its on-screen life in both clocks.

YouTube's caption track is somebody else's transcript, and it has holes
(card_0080: the ASR went silent for 20s+ right where the user clicked). The
rows Cappa OCRs off the screen — stamped by the tracker with their
appear/clear moments and mapped through the bridge to video time — ARE a
transcript of the video as watched. This module records them: one JSONL file
per video under transcripts/, a line appended the moment a row leaves the
screen. It is the durable answer to "the line lived 24:23-24:24, what did
you record?" — and the base a future re-watch/re-click feature can align
against instead of the holey official track.

Best-effort by design: no video id means the row isn't logged (nothing to
file it under), a mapping the bridge can't make leaves the video-time fields
null, and a row still on screen at shutdown is simply not written. Disk
trouble must never touch the card path. No Qt."""

import json
import os
from collections import deque

from ..detection.latency import APPEAR_LAG, CLEAR_LAG

TRANSCRIPTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "transcripts",
)
REPEAT_GAP = 10.0   # the same text clearing again within this many seconds
                    # is a blip loop (something flickering over a PAUSED
                    # frame clears/resurrects the same row endlessly), not a
                    # re-watch: log it once. Deliberate re-reads — a rewind —
                    # clear well apart and are all welcome (user call: every
                    # read is an observation, duplicates always match).


class OcrTranscriptLog:
    """Feed observe() the live Sentence list each scan tick; rows are
    remembered under the video playing when they appeared, and written out
    once they carry a clear stamp (or vanish from the list with one)."""

    def __init__(self, root=TRANSCRIPTS_DIR):
        self._root = root
        self._live = {}    # id(sentence) -> (sentence, video_id)
        self._done = set() # ids written but still listed: never write twice
        self._recent = {}  # video_id -> deque of (text, cleared_monotonic)

    def observe(self, video_id, sentences, video_at=lambda m: None):
        current = set()
        for s in sentences:
            current.add(id(s))
            if (id(s) not in self._live and id(s) not in self._done
                    and video_id and (getattr(s, "text", "") or "").strip()):
                self._live[id(s)] = (s, video_id)
        for key in list(self._live):
            s, vid = self._live[key]
            cleared = getattr(s, "cleared_at", 0.0) or 0.0
            if key in current and cleared <= 0.0:
                continue                    # still on screen
            del self._live[key]
            if cleared <= 0.0:
                continue                    # vanished without a stamp (region
                                            # reset / app noise): log no lie
            if key in current:
                self._done.add(key)         # stamped but still listed (a
                                            # pending clear): remember it so
                                            # the next tick can't re-adopt
                                            # and re-write it
            self._write(vid, s, cleared, video_at)
        self._done &= current               # departed rows may be GC'd and
                                            # their ids recycled: forget them

    def _write(self, video_id, s, cleared, video_at):
        appeared = getattr(s, "appeared_at", 0.0) or 0.0
        rec = {
            "text": s.text,
            "appeared_video": _mapped(video_at, appeared - APPEAR_LAG),
            "cleared_video": _mapped(video_at, cleared - CLEAR_LAG),
            "appeared_monotonic": round(appeared, 3),
            "cleared_monotonic": round(cleared, 3),
        }
        # The id names a file but arrives from the browser: keep only the
        # characters a real YouTube id can hold.
        vid = "".join(ch for ch in str(video_id)
                      if ch.isalnum() or ch in "-_")[:32]
        if not vid:
            return
        recent = self._recent.setdefault(vid, deque(maxlen=8))
        if any(txt == s.text and abs(cleared - at) < REPEAT_GAP
               for txt, at in recent):
            return                          # blip loop, not a re-watch
        recent.append((s.text, cleared))
        try:
            os.makedirs(self._root, exist_ok=True)
            path = os.path.join(self._root, "%s.jsonl" % vid)
            with open(path, "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass


def _mapped(video_at, mono):
    try:
        t = video_at(mono)
    except Exception:
        return None
    return None if t is None else round(t, 3)
