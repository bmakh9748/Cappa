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
HINT_RADIUS = 120.0  # window_hint only trusts sightings this close to the
                     # click: a stock phrase repeated elsewhere in the video
                     # must not lend its timing to this row


class OcrTranscriptLog:
    """Feed observe() the live Sentence list each scan tick; rows are
    remembered under the video playing when they appeared, and written out
    once they carry a clear stamp (or vanish from the list with one)."""

    def __init__(self, root=TRANSCRIPTS_DIR):
        self._root = root
        self._live = {}    # id(sentence) -> (sentence, video_id)
        self._done = set() # ids written but still listed: never write twice
        self._recent = {}  # video_id -> deque of (text, cleared_monotonic)
        self._read = {}    # video_id -> records read back for window_hint

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
        vid = _safe_vid(video_id)
        if not vid:
            return
        recent = self._recent.setdefault(vid, deque(maxlen=8))
        if any(txt == s.text and abs(cleared - at) < REPEAT_GAP
               for txt, at in recent):
            return                          # blip loop, not a re-watch
        recent.append((s.text, cleared))
        if vid in self._read:
            self._read[vid].append(rec)     # keep the hint cache current
        try:
            os.makedirs(self._root, exist_ok=True)
            path = os.path.join(self._root, "%s.jsonl" % vid)
            with open(path, "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass

    # ------------------------------------------------------------- read-back
    def window_hint(self, video_id, text, near_t=None, radius=HINT_RADIUS):
        """The video-time window of a previous SIGHTING of `text` on this
        video — {'start': appeared, 'end': cleared-or-None} — or None. This
        is the payoff of keeping our own transcript ('you should have saved
        where the caption popped up' — card_0009: watch, rewind, pause,
        click): when the row's LIVE appearance is worthless because it was
        born of a seek or a pause, an earlier watch already wrote down its
        real pop. The EARLIEST mapped sighting near the click wins — a seek
        landing mid-row logs a later appearance, never an earlier one, so
        the minimum is the closest thing to the row's true start the log
        holds."""
        vid = _safe_vid(video_id)
        key = _norm_hint(text)
        if not vid or not key:
            return None
        best = None
        for rec in self._records(vid):
            start = rec.get("appeared_video")
            if start is None or not _hint_match(key, rec.get("text")):
                continue
            if near_t is not None and abs(start - near_t) > radius:
                continue
            if best is None or start < best.get("appeared_video"):
                best = rec
        if best is None:
            return None
        end = best.get("cleared_video")
        if end is not None and end < best["appeared_video"]:
            # A row that stayed up ACROSS a seek logs its clear at the
            # landing — an end before its own start (card_0016's log:
            # appeared 249.65, 'cleared' 227.9). The appearance is still
            # the earliest sighting's truth; the end is not.
            end = None
        return {"start": best["appeared_video"], "end": end}

    def _records(self, vid):
        """All records logged for `vid`, read back once and kept current as
        new rows are written. Disk trouble just means no hints."""
        if vid in self._read:
            return self._read[vid]
        recs = []
        try:
            with open(os.path.join(self._root, "%s.jsonl" % vid),
                      encoding="utf-8") as f:
                for line in f:
                    try:
                        recs.append(json.loads(line))
                    except ValueError:
                        continue
        except OSError:
            pass
        self._read[vid] = recs
        return recs


def _safe_vid(video_id):
    """The id names a file but arrives from the browser: keep only the
    characters a real YouTube id can hold."""
    return "".join(ch for ch in str(video_id or "")
                   if ch.isalnum() or ch in "-_")[:32]


def _norm_hint(text):
    """Sightings of one row must match across watches even when OCR jitters
    the punctuation/spacing ('KAN, YO?' vs 'KAN,YO?' — cards 2/3 were the
    same line): compare on letters and digits alone, casefolded."""
    return "".join(ch for ch in (text or "").casefold() if ch.isalnum())


HINT_MIN_CHARS = 6   # a substring match needs this much meat: junk rows
                     # ('C', '0', a clock) must never lend their timing to
                     # a real sentence. Shorter texts still match, but only
                     # by exact equality.


def _hint_match(key, rec_text):
    """Does a logged row's text witness the clicked sentence `key` (both
    normalized)? Equality, or containment either way: the ledger logs each
    ROW of a stacked caption separately while the clicked sentence is the
    joined BLOCK (card_0016: 'AKU CUMA' + 'BSie DITONTON, LHO!' logged as
    two rows, clicked as one sentence) — and a block's rows appear and
    clear together, so any row's life IS the block's life."""
    rk = _norm_hint(rec_text)
    if not rk:
        return False
    if rk == key:
        return True
    if len(rk) < HINT_MIN_CHARS or len(key) < HINT_MIN_CHARS:
        return False
    return rk in key or key in rk


def _mapped(video_at, mono):
    try:
        t = video_at(mono)
    except Exception:
        return None
    return None if t is None else round(t, 3)
