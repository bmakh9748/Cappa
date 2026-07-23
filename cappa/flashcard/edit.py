"""The preview's edit engine: re-window the audio, regrow the sentence.

The preview shows the card; this lets the user CHANGE it before it reaches
Anki. One DraftEditor per shown draft, prepared off the UI thread:

    WORKSPACE   a wider stretch of audio around the card's clip (the clip
                plus up to WORKSPACE_PAD each side), cut ONCE at open --
                from the downloaded source audio when the clip was
                track-timed, from the loopback ring otherwise. The ring
                rolls (~90s), so its cut must happen at open or the past
                is gone; the in-memory samples then make every later
                re-cut and the waveform instant, with no ffmpeg per drag.

    TIMELINE    every word around the clip with a [start, end] guess:
                the card's own sentence words (kind 'core') timed from the
                track sentence when it matched, else spread at the video's
                measured pace; neighbouring words (kind 'neighbor') from
                the caption track, else from this run's OCR transcript
                rows, else absent. Sliding the audio range over the
                timeline is what turns "more audio" into "more sentence",
                word by word -- and a word span back into an audio range.

    COMMITS     recut() rewrites audio.wav from the workspace samples;
                set_sentence_span()/set_text() update the draft's text.
                Every commit records what changed under draft.edited
                (originals kept, so the pipeline's own output stays
                reconstructable) -- the caller rewrites the folder with
                writer.write_artifacts, so Anki always receives the
                edited card.

All times inside one editor live in a single timebase named by `mode`:
'video' (seconds into the video, the caption track's clock) or 'monotonic'
(time.monotonic, the loopback ring's clock) -- the same split
draft.audio_window carries. Qt-free; prepare/recut block and belong on
worker threads, the queries are pure in-memory."""

import os
import tempfile
import time
import wave
from difflib import SequenceMatcher

import numpy as np

from ..detection.sentence import is_cjk
from ..language.translate import clean_word
from . import timing
from .writer import write_artifacts

# How far past the caught clip the editor lets the user reach, each side.
# The user asked for ~20s of slide-room; it also matches SEARCH_RADIUS,
# the neighbourhood the track search itself trusts around a position.
WORKSPACE_PAD = 20.0

# The slider may cut tighter than MIN_CLIP -- the user is choosing on a
# waveform, not clicking blind -- but below this a clip is a glitch, not
# audio.
SEL_MIN = 0.25

# A word joins the sentence when the audio range covers the MIDPOINT of its
# spoken span: reaching a word's middle means most of it was heard, and the
# same rule works symmetrically at both edges and in both directions.
_WORD_MID_TOL = 0.02

# Adopting track word timings for the card's own words needs the track
# sentence to actually BE this sentence: at least this share of the card's
# words must align word-for-word, else the times are spread at pace instead.
_ALIGN_MIN = 0.5


def _norm(word):
    return (clean_word(word) or word or "").casefold()


def _join(words):
    """Join word texts the way the sentence reads: a space between words,
    none between two CJK neighbours (Japanese writes no spaces; the OCR
    sentence never had them)."""
    out = ""
    for w in words:
        if out and not (is_cjk(out[-1:]) and is_cjk(w[:1])):
            out += " "
        out += w
    return out


def _reindex(draft):
    """Where the studied word now sits in the sentence: a word-list index for
    spaced scripts, a character offset for CJK (which writes no spaces) --
    the same two meanings the builder's word_index carries (per-word vs
    per-character hotspot). -1 when the word isn't in the sentence."""
    word = draft.word or ""
    if any(is_cjk(ch) for ch in word):
        draft.word_index = (draft.sentence or "").find(word)
    else:
        target = _norm(word)
        draft.word_index = next(
            (i for i, w in enumerate((draft.sentence or "").split())
             if _norm(w) == target), -1)


def _stale_word_extras(draft):
    """A hand-typed word makes the auto-derived breakdown and TTS reading
    describe the WRONG word -- they were built for the OCR read. Clear them
    rather than ship a wrong anatomy / pronunciation: the card template hides
    an empty field. (Regenerating would need the resolved lemma a typed word
    doesn't carry, plus a network TTS fetch; honest-empty beats wrong.)"""
    draft.breakdown = ""
    path = draft.word_audio_path
    draft.word_audio_path = None
    if path:
        try:
            os.remove(path)
        except OSError:
            pass


def _ensure_edited(draft):
    """The draft's edit record, created on the first edit with the
    pipeline's own output preserved under 'original'."""
    if draft.edited is None:
        draft.edited = {
            "original": {
                "word": draft.word,
                "sentence": draft.sentence,
                "word_translation": draft.word_translation,
                "sentence_translation": draft.sentence_translation,
                "audio_window": dict(draft.audio_window)
                if draft.audio_window else None,
                "audio_seconds": draft.audio_seconds,
            },
            "manual": [],
        }
    return draft.edited


def set_text(draft, field, text, manual=True):
    """Set one text field of the draft (word / sentence /
    word_translation / sentence_translation) and rewrite the folder.
    `manual` marks a hand-typed override (vs slider-driven automation)
    in the edit record. Returns whether anything changed."""
    old = getattr(draft, field)
    if text == old:
        return False
    edited = _ensure_edited(draft)
    setattr(draft, field, text)
    if manual and field not in edited["manual"]:
        edited["manual"].append(field)
    if field in ("word", "sentence"):
        _reindex(draft)   # either change moves where the word sits
    if field == "word":
        _stale_word_extras(draft)
    write_artifacts(draft)
    return True


class DraftEditor:
    """One shown draft's editing state. Build with prepare() on a worker
    thread; `ready` is False (with `error` saying why) when the audio
    cannot be edited -- text edits then still work via set_text."""

    def __init__(self, draft):
        self.draft = draft
        self.ready = False
        self.error = ""
        self.mode = None          # 'video' | 'monotonic' (timebase of all
                                  # times below, per audio_window['source'])
        self.ws_start = 0.0       # workspace bounds, mode timebase
        self.ws_end = 0.0
        self.sel = (0.0, 0.0)     # current clip window inside the workspace
        self.words = []           # [{'text','start','end','kind'}] in order
        self.core = (0, -1)       # index range (inclusive) of the card's
                                  # own sentence words within `words`
        self.span = (0, -1)       # current sentence word span (inclusive)
        self._samples = None      # int16 ndarray (frames, ch) -- workspace
        self._rate = 0
        self._mono_offset = None  # video t -> monotonic: t - ws_start
                                  # + offset (loopback_block_timed only)
        self._env_cache = {}

    # ------------------------------------------------------------- prepare
    @classmethod
    def prepare(cls, draft, source=None, recorder=None):
        """Cut the workspace and build the word timeline. Blocking (may
        wait on the audio download); worker threads only. Never raises:
        a hopeless draft comes back with ready=False and an error."""
        ed = cls(draft)
        win = draft.audio_window or {}
        kind = win.get("source")
        if not draft.folder_path or not draft.audio_path or not kind:
            ed.error = "no audio on this card to edit"
            return ed
        try:
            if kind == "source_audio":
                ed._prepare_source(source, win)
            elif kind in ("loopback_block_timed", "loopback_monotonic"):
                ed._prepare_loopback(source, recorder, win, kind)
            else:
                ed.error = "unknown audio source %r" % kind
        except Exception as exc:
            ed.error = "audio editing unavailable: %s" % exc
            ed.ready = False
        if ed.ready:
            ed._build_timeline(source)
        return ed

    def _prepare_source(self, source, win):
        """Workspace from the downloaded source audio (video timebase)."""
        if source is None:
            self.error = "video source no longer available"
            return
        if source.ensure_audio() is None:
            self.error = "source audio not downloaded"
            return
        self.mode = "video"
        t0, t1 = win["start"], win["end"]
        duration = float((source.meta() or {}).get("duration") or 0.0)
        ws0 = max(0.0, t0 - WORKSPACE_PAD)
        ws1 = t1 + WORKSPACE_PAD
        if duration > ws0:
            ws1 = min(ws1, duration)
        # The workspace is transient (it lives in _samples once loaded), so it
        # goes to a TEMP file, never the card folder -- a discard mid-prep must
        # not leave a stray wav behind that would be swept into Anki.
        fd, ws_path = tempfile.mkstemp(suffix=".wav", prefix="cappa_ws_")
        os.close(fd)
        try:
            source.clip_wav(ws_path, ws0, ws1)
            with wave.open(ws_path, "rb") as w:
                self._rate = w.getframerate()
                frames = w.readframes(w.getnframes())
                ch = w.getnchannels()
            self._samples = np.frombuffer(
                frames, dtype=np.int16).reshape(-1, ch)
        finally:
            try:
                os.remove(ws_path)   # samples live in memory from here on
            except OSError:
                pass
        self.ws_start = ws0
        # Trust the file over the requested window: a cut past the end of
        # the media comes back short.
        self.ws_end = ws0 + self._samples.shape[0] / self._rate
        self.sel = (max(t0, ws0), min(t1, self.ws_end))
        self.ready = self._samples.shape[0] > 0
        if not self.ready:
            self.error = "source audio cut came back empty"

    def _prepare_loopback(self, source, recorder, win, kind):
        """Workspace from the loopback ring. Cut NOW -- the ring rolls."""
        if recorder is None or not getattr(recorder, "ready", False):
            self.error = "audio recorder not running"
            return
        held = recorder.buffered_window()
        if held is None:
            self.error = "no audio buffered"
            return
        t0, t1 = win["start"], win["end"]
        if kind == "loopback_monotonic":
            self.mode = "monotonic"
            m0, m1 = t0, t1
        else:
            # Video-time window, ring audio: keep the editor in video time
            # (the words live there) and remember the linear video->ring
            # offset. monotonic_window refuses a mapping that straddles a
            # loop, trying the full pad first, then narrower reaches.
            self.mode = "video"
            mono = None
            for pad in (WORKSPACE_PAD, 10.0, 4.0, 0.0):
                mono = (source.monotonic_window(max(0.0, t0 - pad), t1 + pad)
                        if source is not None else None)
                if mono is not None:
                    self.ws_start = max(0.0, t0 - pad)
                    break
            if mono is None:
                self.error = "clip window no longer maps to the buffer"
                return
            m0, m1 = mono
            self._mono_offset = m0 - self.ws_start
        if kind == "loopback_monotonic":
            self.ws_start = max(m0 - WORKSPACE_PAD, held[0])
            m_lo = self.ws_start
            m_hi = min(m1 + WORKSPACE_PAD, held[1])
        else:
            m_lo = max(m0, held[0])
            m_hi = min(m1, held[1])
            # The held window can shave the mapped edges; keep video-time
            # bounds in step through the same linear offset.
            self.ws_start = m_lo - self._mono_offset
        if m_hi - m_lo < SEL_MIN:
            self.error = "the audio around this clip has left the buffer"
            return
        got = recorder.clip(m_lo, m_hi)
        if got is None:
            self.error = "no audio in the buffer for that window"
            return
        self._samples, self._rate = got
        self.ws_end = self.ws_start + self._samples.shape[0] / self._rate
        self.sel = (max(t0, self.ws_start), min(t1, self.ws_end))
        self.ready = True

    # ------------------------------------------------------------ timeline
    def _build_timeline(self, source):
        """Core words (the card's sentence, timed as well as the data
        allows) plus neighbours from the track or this run's OCR rows."""
        draft = self.draft
        core_words = draft.sentence.split()
        span0, span1 = self._core_span()
        near_t = (draft.source_meta or {}).get("click_position") \
            if draft.source_meta else None
        starts = self._core_times(core_words, span0, span1, source, near_t)
        core = [{"text": w, "start": s, "end": e, "kind": "core"}
                for w, (s, e) in zip(core_words, starts)]
        before, after = [], []
        if self.mode == "video" and source is not None:
            before = self._neighbors(source, self.ws_start, span0)
            after = self._neighbors(source, span1, self.ws_end)
        self.words = before + core + after
        self.core = (len(before), len(before) + len(core) - 1)
        self.span = self.core

    def _core_span(self):
        """The card sentence's spoken [start, end] in the editor timebase:
        the assembled span when the sentence was rebuilt, else the clip
        window itself (its stamps chose that window in the first place)."""
        draft = self.draft
        if self.mode == "video" and draft.assembled is not None:
            return draft.assembled["start"], draft.assembled["end"]
        return self.sel

    def _core_times(self, core_words, span0, span1, source, near_t):
        """[(start, end)] per core word: the track sentence's own word
        timings where the words align, the measured pace spread between
        anchors elsewhere."""
        n = len(core_words)
        if n == 0:
            return []
        anchors = {}
        if self.mode == "video" and source is not None:
            sent = None
            try:
                sent = source.sentence_for(self.draft.sentence, near_t)
            except Exception:
                sent = None
            if sent and sent.get("words"):
                ours = [_norm(w) for w in core_words]
                track = [_norm(w) for w, _, _ in sent["words"]]
                matcher = SequenceMatcher(None, ours, track)
                for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                    if tag != "equal":
                        continue
                    for k in range(i2 - i1):
                        _, s, e = sent["words"][j1 + k]
                        anchors[i1 + k] = (s, e)
                if len(anchors) < n * _ALIGN_MIN:
                    anchors = {}   # a stray match must not skew the spread
        # Fill the gaps between anchors (and the unanchored whole) by
        # spreading each gap's words evenly across the time between the
        # left anchor's END and the right anchor's START -- the same guess
        # spoken_duration makes, localized.
        out = [None] * n
        prev_i, prev_end = -1, span0
        for idx in sorted(anchors) + [n]:
            right_start = anchors[idx][0] if idx in anchors else span1
            gap = idx - prev_i - 1
            if gap > 0:
                step = max(right_start - prev_end, 0.0) / gap
                for k in range(gap):
                    out[prev_i + 1 + k] = (prev_end + step * k,
                                           prev_end + step * (k + 1))
            if idx in anchors:
                out[idx] = anchors[idx]
                prev_i, prev_end = idx, anchors[idx][1]
        return out

    def _neighbors(self, source, t0, t1):
        """Timed words spoken in [t0, t1] outside the core: the caption
        track's words when there is a track, else this run's OCR rows
        spread at their own on-screen pace. Only words whose MIDPOINT sits
        inside [t0, t1] count -- the core owns its own span. Video
        timebase only."""
        if t1 - t0 <= 0.0:
            return []
        try:
            track = source.words_between(t0, t1)
        except Exception:
            track = []
        if track:
            got = [{"text": w, "start": s, "end": e, "kind": "neighbor"}
                   for w, s, e in track
                   if t0 <= (s + e) / 2.0 <= t1]
        else:
            got = self._row_words(source, t0, t1)
        got.sort(key=lambda w: w["start"])
        return got

    def _row_words(self, source, t0, t1):
        """OCR transcript rows in [t0, t1] exploded into evenly-spread
        words -- the no-track fallback. Rows from another screen band
        (titles, watermarks) are excluded by the same spatial gate the
        sentence assembler uses."""
        from .builder import _boxes_overlap   # the assembler's gate
        try:
            rows = source.rows_between(t0, t1) or []
        except Exception:
            rows = []
        ref_box = self.draft.sentence_box
        out = []
        seen = set()
        for row in rows:
            a, c = row.get("appeared_video"), row.get("cleared_video")
            if a is None or c is None or c <= a:
                continue
            if not _boxes_overlap(ref_box, row.get("box")):
                continue
            words = [w for w in (row.get("text") or "").split() if w]
            if not words:
                continue
            key = (_norm(" ".join(words)), round(a, 1))
            if key in seen:
                continue   # a re-read of the same sighting
            seen.add(key)
            step = (c - a) / len(words)
            for i, w in enumerate(words):
                mid = a + step * (i + 0.5)
                if t0 <= mid <= t1:
                    out.append({"text": w, "start": a + step * i,
                                "end": a + step * (i + 1),
                                "kind": "neighbor"})
        return out

    # ------------------------------------------------------------- queries
    def words_in_range(self, t0, t1):
        """The (i, j) inclusive word span whose spoken MIDPOINTS the range
        covers (the join rule constant above), or None when it covers no
        word."""
        hit = [i for i, w in enumerate(self.words)
               if t0 - _WORD_MID_TOL <= (w["start"] + w["end"]) / 2.0
               <= t1 + _WORD_MID_TOL]
        if not hit:
            return None
        return hit[0], hit[-1]

    def range_for_words(self, i, j):
        """The audio window that carries words i..j: their spoken span plus
        the same safety padding every card clip gets, clamped to the
        workspace."""
        t0 = self.words[i]["start"] - timing.PREROLL
        t1 = self.words[j]["end"] + timing.POSTROLL
        return max(t0, self.ws_start), min(t1, self.ws_end)

    def sentence_text(self, i, j):
        """The sentence words i..j read as text (CJK joins spaceless)."""
        return _join([w["text"] for w in self.words[i:j + 1]])

    def clicked_word_index(self, word_text):
        """The global timeline index of the core chip that is the studied
        word, or -1. Found by TEXT, not the card's word_index (which is a
        per-character offset on CJK and would point at the wrong chip)."""
        if not word_text:
            return -1
        lo, hi = self.core
        target = _norm(word_text)
        for i in range(lo, hi + 1):
            if _norm(self.words[i]["text"]) == target:
                return i
        for i in range(lo, hi + 1):    # CJK: one core chip may BE the whole
            if word_text in self.words[i]["text"]:   # spaceless sentence
                return i
        return -1

    def clamp_selection(self, t0, t1):
        """A drag result forced legal: inside the workspace, at least
        SEL_MIN long."""
        t0 = min(max(t0, self.ws_start), self.ws_end - SEL_MIN)
        t1 = min(max(t1, t0 + SEL_MIN), self.ws_end)
        return t0, t1

    def envelope(self, bins):
        """Per-bin waveform peaks over the workspace, 0..1, for painting.
        Cached per bin count."""
        if bins in self._env_cache:
            return self._env_cache[bins]
        if self._samples is None or not len(self._samples):
            return [0.0] * bins
        mono = np.abs(self._samples).max(axis=1)
        edges = np.linspace(0, len(mono), bins + 1).astype(int)
        peaks = [float(mono[a:b].max()) if b > a else 0.0
                 for a, b in zip(edges, edges[1:])]
        top = max(peaks) or 1.0
        env = [p / top for p in peaks]
        self._env_cache[bins] = env
        return env

    # ------------------------------------------------------------- commits
    def recut(self, t0, t1):
        """Rewrite audio.wav as [t0, t1] of the workspace and record the
        edit. Blocking (file write); worker threads only. Returns the new
        clip seconds (0.0 when the slice came back empty -- the draft is
        then left as it was)."""
        if not self.draft.folder_path:
            return 0.0                   # discarded under us -- nothing to cut
        t0, t1 = self.clamp_selection(t0, t1)
        i0 = int(round((t0 - self.ws_start) * self._rate))
        i1 = int(round((t1 - self.ws_start) * self._rate))
        data = self._samples[max(0, i0):max(0, i1)]
        if not len(data):
            return 0.0
        draft = self.draft
        path = draft.audio_path or os.path.join(draft.folder_path,
                                                "audio.wav")
        for attempt in (0, 1):
            try:
                with wave.open(path, "wb") as w:
                    w.setnchannels(data.shape[1])
                    w.setsampwidth(2)   # int16
                    w.setframerate(self._rate)
                    w.writeframes(data.tobytes())
                break
            except OSError:
                # The play button may briefly hold the old file open.
                if attempt:
                    raise
                time.sleep(0.2)
        self.sel = (t0, t1)
        edited = _ensure_edited(draft)
        edited["audio"] = {"start": round(t0, 3), "end": round(t1, 3)}
        seconds = data.shape[0] / self._rate
        draft.audio_path = path
        draft.audio_seconds = seconds
        # audio_window keeps describing what audio.wav IS (same keys, the
        # original saved under edited['original']).
        if draft.audio_window is not None:
            draft.audio_window["start"] = t0
            draft.audio_window["end"] = t1
        write_artifacts(draft)
        return seconds

    def set_sentence_span(self, i, j):
        """Commit words i..j as the card's sentence (the slider path --
        not a manual override). Rewrites the folder; returns whether the
        text changed."""
        self.span = (i, j)
        return set_text(self.draft, "sentence", self.sentence_text(i, j),
                        manual=False)
