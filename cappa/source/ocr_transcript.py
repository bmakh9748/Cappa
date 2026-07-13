"""Cappa's own transcript of what it WATCHED: every caption row the tracker
confirmed, written down with its on-screen life in both clocks.

YouTube's caption track is somebody else's transcript, and it has holes
(card_0080: the ASR went silent for 20s+ right where the user clicked). The
rows Cappa OCRs off the screen — stamped by the tracker with their
appear/clear moments and mapped through the bridge to video time — ARE a
transcript of the video as watched. This module records them: one JSONL file
per video under transcripts/, a line appended the moment a row leaves the
screen. It is the durable answer to "the line lived 24:23-24:24, what did
you record?".

The files are a RECORD, never an oracle. window_hint() — the recall a card
leans on when its own appearance was born of a pause or a seek — answers
only from what THIS run of the app watched, and that memory dies with the
process (user call, 2026-07-08: "you can take from only this instance of
the app being open... when i reopen, taking from the past times its been
open is bad"). A card's timing may cite a caption this run saw pop; never
one a file remembers.

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
                    # EXEMPT across playback passes: a Short under 10 s loops
                    # INSIDE this window, and every pass after the first was
                    # being discarded as a blip — a row seen in a NEW pass
                    # (the bridge's pass counter moved) is a genuine
                    # observation however soon it recurs.
HINT_RADIUS = 120.0  # window_hint only trusts sightings this close to the
                     # click: a stock phrase repeated elsewhere in the video
                     # must not lend its timing to this row
SIGHTING_GAP = 2.0   # wall-clock seconds separating one sighting of a row
                     # from the next. A caption that types on is re-read as
                     # it grows and logs a CHAIN of rows ('NUMPANG' ->
                     # 'NUMPANG DI HELIKOPTER' -> '... KEBALIK'); in
                     # card_0025's log those follow each other within
                     # 0.03-1.13 s, while the nearest re-watch of the same
                     # line sits 3.3 s away. 2 s tells the two apart.
MIN_RATE_LIFE = 0.25  # a row on screen for less than this measures detector
                      # lag, not speech: it lends no pace to seconds_per_word
MIN_RATE_ROWS = 5     # ...and one video needs this many before its own pace
                      # is trusted over the default


class OcrTranscriptLog:
    """Feed observe() the live Sentence list each scan tick; rows are
    remembered under the video playing when they appeared, and written out
    once they carry a clear stamp (or vanish from the list with one)."""

    def __init__(self, root=TRANSCRIPTS_DIR):
        self._root = root
        self._live = {}    # id(sentence) -> (sentence, video_id)
        self._done = set() # ids written but still listed: never write twice
        self._recent = {}  # video_id -> deque of (text, cleared_monotonic)
        self._seen = {}    # video_id -> rows THIS RUN watched: window_hint's
                           # only source, gone when the process is

    def observe(self, video_id, sentences, video_at=lambda m: None,
                pass_id=None):
        # A falsy video_id means there is no live video to attribute these
        # rows to -- paused, tab hidden/closed, or no bridge (the caller,
        # source_wiring.live_video_id, decides). Nothing is logged then, so
        # a frozen or off-screen frame can't lie about a caption's life.
        # `pass_id` is the bridge's playthrough counter (None without one):
        # the REPEAT_GAP dedupe only swallows a repeat within the SAME pass.
        current = set()
        for s in sentences:
            current.add(id(s))
            if (id(s) not in self._live and id(s) not in self._done
                    and video_id and (getattr(s, "text", "") or "").strip()
                    # A watermark/clock/URL is on screen, but it is not a
                    # caption and this file is a transcript of captions.
                    and not getattr(s, "junk", None)):
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
            self._write(vid, s, cleared, video_at, pass_id)
        self._done &= current               # departed rows may be GC'd and
                                            # their ids recycled: forget them

    def _write(self, video_id, s, cleared, video_at, pass_id=None):
        appeared = getattr(s, "appeared_at", 0.0) or 0.0
        conf = getattr(s, "ocr_conf", None)
        box = getattr(s, "box", None)
        rec = {
            "text": s.text,
            # The row's on-screen rectangle (region-local physical px), so the
            # sentence assembler can tell a caption in the SAME place from one
            # elsewhere on screen: a row whose box doesn't overlap the clicked
            # caption is not part of its sentence (user rule — the title up top
            # is not the caption at the bottom, however the words line up).
            "box": [int(v) for v in box] if box is not None else None,
            "appeared_video": _mapped(video_at, appeared - APPEAR_LAG),
            "cleared_video": _mapped(video_at, cleared - CLEAR_LAG),
            "appeared_monotonic": round(appeared, 3),
            "cleared_monotonic": round(cleared, 3),
            # The read's confidence, so "was OCR unsure?" can be checked
            # against a row's on-screen LIFE (cleared-appeared): the open
            # question is whether captions too quick to read come back
            # low-confidence (a usable trigger) or just confidently wrong /
            # missing entirely (not one). An added key, never a renamed one.
            "ocr_conf": round(conf, 3) if conf is not None else None,
        }
        vid = _safe_vid(video_id)
        if not vid:
            return
        recent = self._recent.setdefault(vid, deque(maxlen=8))
        if any(txt == s.text and abs(cleared - at) < REPEAT_GAP
               and p == pass_id
               for txt, at, p in recent):
            return                          # blip loop, not a re-watch —
                                            # a repeat in a NEW pass (the
                                            # Short looped) is welcome
        recent.append((s.text, cleared, pass_id))
        # This run's in-memory record -- what window_hint and
        # seconds_per_word read back. Deduped like the file: a paused frame's
        # flicker must not lend a phantom pace or a phantom sighting.
        self._seen.setdefault(vid, []).append(rec)
        try:
            os.makedirs(self._root, exist_ok=True)
            path = os.path.join(self._root, "%s.jsonl" % vid)
            with open(path, "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass

    # ------------------------------------------------------------- read-back
    def window_hint(self, video_id, text, near_t=None, radius=HINT_RADIUS):
        """The video-time window {'start', 'end'} of a sighting of `text`
        THIS RUN watched, or None. Only this process's own observations
        count — the transcripts/ files are never read back (see the module
        docstring); a fresh app knows nothing until it watches something.

        Used when a row's LIVE appearance is worthless because it was born
        of a pause or a seek (card_0009: watch, rewind, pause, click). The
        EARLIEST mapped sighting near the click supplies the start: a seek
        landing mid-row logs a later appearance, never an earlier one, so
        the minimum is the closest thing to the row's true pop."""
        vid = _safe_vid(video_id)
        key = _norm_hint(text)
        if not vid or not key:
            return None
        seen = [rec for rec in self._seen.get(vid, ())
                if rec.get("appeared_video") is not None
                and _hint_match(key, rec.get("text"))
                and (near_t is None
                     or abs(rec["appeared_video"] - near_t) <= radius)]
        if not seen:
            return None
        best = min(seen, key=lambda rec: rec["appeared_video"])
        start = best["appeared_video"]
        return {"start": start, "end": _sighting_end(seen, best, start)}

    def rows_between(self, video_id, t0, t1):
        """This run's rows whose on-screen life touches video-time [t0, t1],
        time-ordered — the raw material the sentence assembler merges with
        the caption track. Same session-only source as window_hint."""
        out = [rec for rec in self._seen.get(_safe_vid(video_id), ())
               if rec.get("appeared_video") is not None
               and rec.get("cleared_video") is not None
               and rec["cleared_video"] >= t0
               and rec["appeared_video"] <= t1]
        out.sort(key=lambda rec: rec["appeared_video"])
        return out

    def seconds_per_word(self, video_id):
        """How long one spoken word lasts in THIS video, or None until
        enough rows have been watched. The median of (a row's on-screen
        life / its word count) over the rows this run saw: self-calibrating
        per video, so a fast talker and a slow one each get their own pace
        without anyone naming a speaker (we cannot tell them apart, and do
        not need to -- one video, one pace).

        Only multi-word rows count: a one-word chunk's life is dominated by
        the detector's own lags, not by speech."""
        rows = self._seen.get(_safe_vid(video_id), ())
        rates = []
        for rec in rows:
            words = len((rec.get("text") or "").split())
            life = rec["cleared_monotonic"] - rec["appeared_monotonic"]
            if words >= 2 and life > MIN_RATE_LIFE:
                rates.append(life / words)
        if len(rates) < MIN_RATE_ROWS:
            return None
        rates.sort()
        return rates[len(rates) // 2]


def _sighting_end(seen, best, start):
    """When the caption `best` began actually LEFT the screen: the last
    clear of the sighting `best` opened. A line that types on is re-read as
    it grows, so one sighting logs a chain of rows within SIGHTING_GAP of
    each other, and the first row's clear is merely the moment it grew
    (card_0025 closed there and lost 1.1 s — the clip ended before 'DI
    HELIKOPTER KEBALIK' was ever spoken). Rows of a stacked block appear
    together and belong to the same chain for the same reason.

    A clear mapped BEFORE its own start spans a seek (card_0016's log:
    appeared 249.65, 'cleared' 227.9) and is no clear at all. None when the
    sighting has no usable end -- the caller then bounds the clip itself."""
    edge = best.get("cleared_monotonic", 0.0)
    end = None
    for rec in sorted(seen, key=lambda r: r.get("appeared_monotonic", 0.0)):
        appeared = rec.get("appeared_monotonic", 0.0)
        if appeared < best.get("appeared_monotonic", 0.0):
            continue                      # an earlier viewing of the row
        if appeared - edge > SIGHTING_GAP:
            break                         # the screen moved on; a re-watch
        edge = max(edge, rec.get("cleared_monotonic", 0.0))
        cleared = rec.get("cleared_video")
        if cleared is not None and cleared >= start:
            end = cleared if end is None else max(end, cleared)
    return end


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
