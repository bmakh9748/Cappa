"""Choose the audio window and cut one card's clip.

THE WINDOW RULE (user spec, 2026-07-09 — 'it is not complicated'): the clip
is the clicked caption BLOCK's on-screen life. Every row of the block is
looked up in this run's transcript (plus its live ledger stamps); the
EARLIEST appearance is the start and the LATEST clear is the end, used as-is
— those stamps are already detector-lag-corrected, so no buffer is added
(HEAD_BUFFER/TAIL_TRIM are 0; user call 2026-07-09c, card_0006). Taking the
earliest logged sighting absorbs detection churn: a row the ledger cleared
and re-accepted mid-life (card_0002: animated art next to the glyphs)
anchors at the caption's real pop, not its rebirth.

THE CAPTION TRACK FILLS AND EXTENDS THE END (user amend, 2026-07-09b/c):
when the track's own words strongly match the sentence, its window fills an
edge we never observed, and extends the END outward when our clear came
early (churn / a quick click loses the tail). The START, though, is floored
at our observed appearance — the words weren't on screen before we saw the
caption pop, so an earlier track tag is just ASR lead and must not pull the
clip onto the previous sentence (card_0006). window_for gives the word-exact,
de-phantomed span of THIS sentence, matched to the same occurrence, so it
can't wander into a neighbour. (The one case this gets wrong — our OWN
appearance being a churn/pause fragment, card_0004 — is the deferred
reconciliation in PLAN.md.) The track is still also _snap_to_track's ground
truth for OCR misreads. The user's min/max clip settings clamp last, the cap
centring on the click.

Where the audio itself comes from, best first:

    1. the window cut from the DOWNLOADED source audio
       (works paused and on any past line)
    2. the same window cut from the LOOPBACK ring buffer via the
       bridge's video-time -> clock-time mapping (download not ready)
    3. OCR-timed loopback (no video source at all)

Every degradation writes a draft note, never raises. Qt-free; the window
maths (lags, min/max clip) live in timing.py."""

import os
import time

from . import timing
from .timing import audio_window, shrink_to_max, widen_to_min

# A track text match at or above this is trusted as ground truth for what
# the caption SAID (never for timing): _snap_to_track corrects OCR misreads
# from it when the track is human-made (card_0018).
SOURCE_STRONG_SCORE = 0.75

# No buffer around the block's on-screen life (user call, 2026-07-09c,
# card_0006): the transcript's appeared/cleared are ALREADY corrected for
# the detector's reaction time (ocr_transcript writes appeared_video with
# APPEAR_LAG subtracted; the live-stamp path subtracts it too), so a head
# buffer starts the clip too early and a tail trim chops the last word.
# Kept as named constants so a deliberate ring-out lead/tail can be dialed
# back in later without hunting the arithmetic.
HEAD_BUFFER = 0.0
TAIL_TRIM = 0.0

# Slack when deciding whether a track match is the SAME occurrence as the
# on-screen life it would extend (vs. a duplicate line elsewhere). A
# fragment we detected sits inside the true window, so a real match always
# overlaps; this only forgives a small gap when one edge is a single point.
SAME_OCCURRENCE_TOL = 1.0

# A loopback clip whose int16 peak is at or under this is silence, not speech
# (card_0027 came out at peak 1 of 32767: the bound endpoint wasn't the one
# playing). Such a clip is DISCARDED — a silent wav on a card is worse than
# no audio — and the note says what happened.
SILENT_CLIP_PEAK = 100

# NOTE on stamps: a mapped appearance/clear is ALREADY corrected for the
# pipeline's measured reaction time (detection/latency.py APPEAR_LAG /
# CLEAR_LAG, subtracted before the bridge mapping). Those measured numbers
# plus the user's HEAD_BUFFER/TAIL_TRIM are the ONLY time ever added or
# taken — no worst-case padding (deleted 2026-07-09, user call: the same
# row watched three times stamped within ~170 ms).
# Hardsubs leave with their speech: nothing past the sentence's on-screen
# END belongs on the card. A SEEN clear needs no buffer — its stamp already
# trails the real vanish (user call); only the pause path, where the line
# is still up and the true end unknown, gets this small tail past the click.
PAUSE_TAIL = 0.4
# A mid-life click on a PLAYING video may wait this long for the row's
# vanish before the clip's end is chosen: the source audio is a FILE (it
# can wait; the old loopback ring buffer couldn't), and the clear stamp is
# the sentence's true end where the click is just wherever the user's mouse
# got there first (card_0077: 'the line left at 13:55 — that's what should
# have been recorded'). Bounded so a long-lived line can't stall the card.
CLEAR_WAIT = 2.0
_WAIT_STEP = 0.1


def write_audio(draft, sentence, recorder, now, source=None, near_t=None):
    """Write the draft's audio.wav from the best available source (the ladder
    in the module docstring), recording every skip/degradation in
    draft.notes."""
    # Preferred: caption-track timing (source audio, or loopback mapped to the
    # caption's clock time). Falls through to OCR-timed loopback when there's
    # no source, no match, or no audio anywhere.
    if source is not None and _write_from_source(
            draft, sentence, source, near_t, recorder):
        return

    # A session that never learned of ANY video means the click wasn't on a
    # YouTube video Cappa knows about — whatever the loopback caught then is
    # unrelated system audio, worse than no audio at all. (source=None means
    # the caller doesn't do video sourcing; the loopback is all it has.)
    if source is not None and getattr(source, "status", "") in ("idle",
                                                                "bad URL"):
        draft.notes.append("no YouTube video detected — audio not recorded")
        return

    if recorder is None:
        draft.notes.append("audio recorder not running")
        return
    if not getattr(recorder, "ready", False):
        err = getattr(recorder, "error", "")
        draft.notes.append("audio: %s" % err if err else
                           "audio recorder not running")
        return

    t0, t1 = audio_window(sentence, now)
    draft.audio_window = {
        "source": "loopback_monotonic",
        "start": t0,
        "end": t1,
    }
    wav_path = os.path.join(draft.folder_path, "audio.wav")
    try:
        secs = recorder.save_wav(wav_path, t0, t1)
    except Exception as exc:
        secs = 0.0
        draft.notes.append("audio save failed: %s" % exc)

    if secs > 0.0:
        if not _drop_if_silent(draft, recorder, wav_path):
            draft.audio_path = wav_path
            draft.audio_seconds = secs
    else:
        draft.notes.append("no audio in the buffer for that window")


def _await_clear(sentence, source, timeout=CLEAR_WAIT):
    """Give tracking a beat to stamp a mid-life row's vanish before any
    clip end is chosen. Only while the browser definitely reports PLAYING:
    a paused video keeps its row frozen on screen (card_0082) — no clear is
    coming and the pause already marks the spoken-so-far end — and an
    unknown state (no bridge) must not stall the clipboard-only path."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if (getattr(sentence, "cleared_at", 0.0) or 0.0) > 0.0:
            return
        if getattr(source, "is_paused", lambda: None)() is not False:
            return
        time.sleep(_WAIT_STEP)


def _live_end(source, near_t, sentence=None, t_appear=None):
    """Where a row STILL on screen after the wait finishes being SPOKEN.

    The old bound was 'the freshest playback position, plus a flat 0.4s'.
    That treats a fifteen-word line that popped 100 ms ago exactly like a
    four-word one, and it quietly assumes the clicked word is the word being
    spoken — it rarely is, since the user reads the line first and clicks
    after (user call, 2026-07-08). Instead: a line takes `words × this
    video's measured pace` to say, so it ends that long after it APPEARED,
    whenever the click landed inside it. Playback's own position is still the
    floor — audio already heard is audio the line covered.

    Falls back to the flat tail when the line's appearance or word count is
    unknown. Cutting PAST the click is legal because the clip comes from the
    downloaded source file; the loopback rescue clamps itself (see
    _loopback_rescue), since a ring buffer cannot hold audio not yet played."""
    try:
        t = getattr(source, "play_time", lambda: None)()
    except Exception:
        t = None
    if t is None or not (near_t <= t <= near_t + CLEAR_WAIT + 3.0):
        t = near_t
    heard = t + timing.MIN_LIVE_TAIL       # everything up to now was the line
    words = len(getattr(sentence, "words", ()) or ())
    if t_appear is None or not words:
        return t + PAUSE_TAIL
    rate = getattr(source, "seconds_per_word", lambda: None)()
    predicted = t_appear + timing.spoken_duration(words, rate)
    return max(heard, predicted)


def _same_occurrence(track, seen_start, seen_end, near_t):
    """True when `track` (a window_for match) describes the SAME on-screen
    caption we timed, not a duplicate of the line elsewhere in the video.
    The anchor is the on-screen life when we have it, else the click; the
    track must overlap it within SAME_OCCURRENCE_TOL."""
    if seen_start is not None:
        lo = seen_start
        hi = seen_end if seen_end is not None else seen_start
    elif near_t is not None:
        lo = hi = near_t
    else:
        return True   # nothing to anchor against; trust the text match
    return (track["start"] - SAME_OCCURRENCE_TOL <= hi
            and lo <= track["end"] + SAME_OCCURRENCE_TOL)


def _write_from_source(draft, sentence, source, near_t=None, recorder=None):
    """Cut the card's audio from the clicked block's ON-SCREEN LIFE (the
    window rule in the module docstring). Returns True on success; False
    falls back to OCR-timed loopback, and every skip leaves a note so a
    degraded card is never silent about why."""
    meta = {}
    try:
        meta = source.meta()
    except Exception:
        pass

    # A caption-less video is a NOTE, not a failure (user call, card_0002):
    # the screen times the clip and the downloaded audio still gets cut —
    # the card just says why its timing had no track to lean on.
    if str(getattr(source, "status", "")).startswith("no captions"):
        draft.notes.append("video has no captions — clip timed from the "
                           "on-screen life")

    # The caption track's window for THIS sentence's text (window_for =
    # the exact matched words, not the run-on position blob). When it
    # strongly matches it IS the spoken sentence, and it's the fallback the
    # on-screen life leans on for any edge detection missed (user call,
    # 2026-07-09b). Also _snap_to_track's ground truth for OCR misreads.
    track = None
    text = getattr(sentence, "text", "") or ""
    if text:
        try:
            track = source.window_for(text, near_t=near_t)
        except Exception:
            track = None
    strong_track = bool(track) and track.get("score", 0.0) >= SOURCE_STRONG_SCORE

    assembled = getattr(draft, "assembled", None)
    if assembled is not None:
        # The builder already rebuilt the full word-at-a-time sentence and
        # its span (our transcript's timings first, the track's for what we
        # missed): that span IS the block's life. No clear to await — the
        # sentence's end is known even if the last chunk hasn't shown yet.
        t_start, t_end = assembled["start"], assembled["end"]
        start_from = end_from = "assembled"
    else:
        # A row clicked mid-life may be about to vanish — its clear stamp
        # is the truest clip end there is (card_0077).
        _await_clear(sentence, source)
        # Every row of the clicked block: its logged sightings this run
        # (which recall the caption's REAL pop when the live row is a
        # churn rebirth, card_0002, or a seek landing, card_0009) plus its
        # live ledger stamps, all mapped to video time. Earliest in,
        # latest out — that's the ON-SCREEN life.
        rows = list(getattr(sentence, "lines", None) or [sentence])
        starts, ends = [], []
        for row in rows:
            row_text = getattr(row, "text", "") or ""
            seen = None
            if row_text:
                try:
                    seen = getattr(source, "sighting_window",
                                   lambda t, near_t=None: None)(
                        row_text, near_t)
                except Exception:
                    seen = None
            if seen and seen.get("start") is not None:
                starts.append(seen["start"])
                if seen.get("end") is not None:
                    ends.append(seen["end"])
            appeared = getattr(row, "appeared_at", 0.0) or 0.0
            if appeared > 0.0:
                t = getattr(source, "video_time_at", lambda m: None)(
                    appeared - timing.APPEAR_LAG)
                if t is not None:
                    starts.append(t)
            cleared = getattr(row, "cleared_at", 0.0) or 0.0
            if cleared > 0.0:
                t = getattr(source, "video_time_at", lambda m: None)(
                    cleared - timing.CLEAR_LAG)
                if t is not None:
                    ends.append(t)
        seen_start = min(starts) if starts else None
        seen_end = max(ends) if ends else None

        # On-screen life is primary. The matched track fills an edge we
        # never saw, and EXTENDS the END outward — our clear is often lost
        # early (churn, a quick click, a re-detection), so the sentence can
        # keep being spoken past it. But the START is floored at our own
        # observed appearance: the caption's words weren't on screen before
        # we saw it pop, so an EARLIER track start is just the ASR's lead
        # and must not pull the clip back (card_0006: we cleanly saw the
        # line appear at 49.05, the auto track's 'jangan' tag sat at 48.42,
        # and the clip opened on the previous sentence).
        #
        # (When our own appearance is ITSELF a fragment — a churn/pause hid
        # the real pop, card_0004 — this floor is wrong and the track's
        # earlier start was right; telling the two apart is the deferred
        # reconciliation logged in PLAN.md. For now the clean-appearance
        # case wins.)
        #
        # Only the SAME occurrence extends: window_for can return a DUPLICATE
        # of this line elsewhere in the video, and that far window must not
        # hijack the clip. "Same" = the track overlaps the on-screen life
        # (or, with no life, sits around the click).
        t_start, start_from = seen_start, "onscreen"
        t_end, end_from = seen_end, "onscreen"
        if strong_track and _same_occurrence(track, seen_start, seen_end,
                                             near_t):
            if t_start is None:                       # fill only, never earlier
                t_start, start_from = track["start"], "track"
            if t_end is None or track["end"] > t_end:  # fill or extend later
                t_end, end_from = track["end"], "track"

        if t_start is None:
            draft.notes.append(
                "the block's on-screen life could not be mapped to video "
                "time, and the caption track had no match"
                + ("" if near_t is not None else " (no browser position)"))
            return False
        if t_end is None:
            # No clear and no track: predict where the line finishes being
            # spoken (words × this video's measured pace).
            t_end = _live_end(source, near_t, sentence, t_start)
            end_from = "predicted"

    match = {"start": t_start, "end": t_end,
             "by": "assembled" if assembled is not None else "block_life"}

    draft.source_meta = {
        "video_id": meta.get("video_id"),
        "url": meta.get("url"),
        "title": meta.get("title"),
        "channel": meta.get("channel"),
        "caption_lang": meta.get("caption_lang"),
        "caption_auto": meta.get("caption_auto"),
        "matched_by": match["by"],
        "match_score": round((track or {}).get("score", 0.0), 3),
        "caption_text": (track or {}).get("text", ""),
        "start_seconds": round(match["start"], 3),
        "start_from": start_from,
        "end_seconds": round(match["end"], 3),
        "end_from": end_from,
    }
    if strong_track:
        draft.source_meta["track_window"] = [round(track["start"], 3),
                                             round(track["end"], 3)]
    if near_t is not None:
        draft.source_meta["click_position"] = round(near_t, 3)

    # The user's buffers, then the user's min/max clip settings — the cap
    # centres on the click so the clicked word stays inside a long block.
    start = max(0.0, match["start"] - HEAD_BUFFER)
    end = match["end"] - TAIL_TRIM
    start, end = widen_to_min(start, end, timing.MIN_CLIP, floor=0.0)
    start, end = shrink_to_max(start, end, timing.max_clip(), center=near_t)

    wav_path = os.path.join(draft.folder_path, "audio.wav")
    window = {
        "source": "source_audio",
        "matched_by": match["by"],
        "start": start,
        "end": end,
        "head_buffer": HEAD_BUFFER,
        "tail_trim": TAIL_TRIM,
        "score": draft.source_meta["match_score"],
        "caption_text": draft.source_meta["caption_text"],
        "lang": meta.get("caption_lang"),
        "auto": meta.get("caption_auto"),
    }

    # First choice: the downloaded source audio. ensure_audio waits briefly
    # for an in-flight download and retries a previously failed one.
    ensure = getattr(source, "ensure_audio", None)
    if ensure is not None:
        try:
            ensure()
        except Exception:
            pass
    try:
        secs = source.clip_wav(wav_path, start, end)
    except Exception as exc:
        draft.notes.append("source audio unavailable: %s" % exc)
        secs = 0.0
    if secs > 0.0:
        draft.audio_path = wav_path
        draft.audio_seconds = secs
        draft.audio_window = window
        return True

    # Rescue: the same window, cut from the loopback buffer at the clock
    # times the line actually played -- it just left the speakers.
    secs = _loopback_rescue(draft, source, recorder, window, wav_path)
    if secs > 0.0:
        window["source"] = "loopback_block_timed"
        draft.audio_window = window
        if not _drop_if_silent(draft, recorder, wav_path):
            draft.audio_path = wav_path
            draft.audio_seconds = secs
            draft.notes.append("audio cut from loopback with on-screen "
                               "timing (source download unavailable)")
        # Silent or not, the loopback buffer is the only audio there is:
        # an OCR-timed cut of the same buffer would be just as silent.
        return True
    return False


def _drop_if_silent(draft, recorder, wav_path):
    """Discard a loopback clip that recorded (near) nothing: the sound went
    somewhere the recorder wasn't listening -- muted tab, or Windows routing
    audio to a device other than the bound one. A silent wav on a card is
    worse than no audio. True when the clip was dropped."""
    peak = getattr(recorder, "last_clip_peak", None)
    if peak is None or peak > SILENT_CLIP_PEAK:
        return False
    try:
        os.remove(wav_path)
    except OSError:
        pass
    draft.notes.append(
        "audio discarded: the recording was silent (peak %d) — was the "
        "video muted, or playing on a different output device?" % peak)
    return True


def _loopback_rescue(draft, source, recorder, window, wav_path):
    """Cut the caption window (the widened dict written to provenance) from
    the loopback ring buffer via the bridge's video-time -> clock-time
    mapping. 0.0 when impossible."""
    if recorder is None or not getattr(recorder, "ready", False):
        return 0.0
    mono = getattr(source, "monotonic_window", None)
    if mono is None:
        return 0.0
    clock = mono(window["start"], window["end"])
    if clock is None:
        return 0.0
    t0, t1 = clock
    try:
        return recorder.save_wav(wav_path, t0, t1)
    except Exception as exc:
        draft.notes.append("loopback rescue failed: %s" % exc)
        return 0.0
