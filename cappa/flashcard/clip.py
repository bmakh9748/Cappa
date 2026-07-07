"""Choose the audio window and cut one card's clip.

The builder hands this module a clicked sentence and everything known about
the moment (recorder, SourceSession, playback position); it decides WHERE the
audio lives and writes audio.wav. The ladder, best source first:

    1. caption-track window cut from the DOWNLOADED source audio
       (works paused and on any past line)
    2. no caption near the click at all (ASR holes run 20s+): a window built
       from the row's ON-SCREEN life, still cut from the source audio
    3. the same window cut from the LOOPBACK ring buffer via the
       bridge's video-time -> clock-time mapping (download not ready)
    4. OCR-timed loopback (no video source at all)

Window choice within 1/2 is `choose_window`: the playback position is the
boss, a text match must be strong and near it (or overlap the position
window) to win. Every degradation writes a draft note, never raises.

Qt-free; the window maths (lags, pre/postroll, min/max clip) live in
timing.py."""

import os
import time

from . import timing
from .timing import (SOURCE_POSTROLL, SOURCE_PREROLL, audio_window,
                     shrink_to_max, widen_to_min)

# A text match is trusted over the raw playback position only when it is both
# strong and lands near where we actually are -- otherwise a wrong auto-caption
# nearby could still shift the clip. Below that, the position window wins.
SOURCE_STRONG_SCORE = 0.75
SOURCE_POS_TOL = 6.0   # seconds a trusted text match may sit from near_t

# A loopback clip whose int16 peak is at or under this is silence, not speech
# (card_0027 came out at peak 1 of 32767: the bound endpoint wasn't the one
# playing). Such a clip is DISCARDED — a silent wav on a card is worse than
# no audio — and the note says what happened.
SILENT_CLIP_PEAK = 100

# A position-matched clip CENTERS this far before the clicked row's
# appearance stamp. The stamp trails the row's actual speech by a variable
# lag — settle debounce, scan interval, clock-mapping slack — measured on
# the same row watched twice: card_0069 stamped 823.3 and card_0071 stamped
# 824.3 for a row whose cue ran 822.6-823.5. So the clicked word's audio
# lies BEFORE the stamp far more often than after; a window centred a
# second behind it covers both watches, where a forward window missed the
# word entirely both times.
APPEAR_BACKSHIFT = 1.0
# When a trusted appearance exists, the position window's START is cut back
# to backshift + this grace behind it: speech begins just before the row
# pops and stamps run late, but everything earlier belongs to the PREVIOUS
# sentences the ASR run-on dragged in (cards 3/5, 2026-07-07).
APPEAR_TRIM_GRACE = 0.8
# An appearance mapped LATER than where the user paused is impossible for a
# row they were reading — it's a re-read's stamp or a paused-seek mapping
# artifact; past this slack the click position anchors the clip instead.
APPEAR_PAST_CLICK_TOL = 1.0
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


def _live_end(source, near_t):
    """The spoken-so-far bound for a row STILL on screen after the wait:
    playback moved past the click while _await_clear listened, so the
    freshest position — not the click — marks how much of the line has
    been heard. The click stays the bound when the bridge can't say, or
    reports something implausible (a seek away)."""
    try:
        t = getattr(source, "play_time", lambda: None)()
    except Exception:
        t = None
    if t is None or not (near_t <= t <= near_t + CLEAR_WAIT + 3.0):
        t = near_t
    return t + PAUSE_TAIL


def _appearance(sentence, source, near_t, draft=None):
    """The clicked row's appearance stamp mapped to video time, or None when
    the bridge can't say — or when the stamp maps AFTER the click plus
    tolerance (a re-read's stamp or a paused-seek mapping artifact, not the
    row the user was reading: APPEAR_PAST_CLICK_TOL). This is the priority
    signal: when it exists, the row's SEEN life outranks the auto track.

    A stamp only means "the sentence starts here" if the row popped in
    during CONTINUOUS playback. A row that appeared because the user paused
    or seeked was already mid-life — its true start was never seen, and
    nothing may be assumed about it (user rule; cards 2-3 of 2026-07-07
    clicked one re-watched line twice: the seek-landing stamp hijacked a
    good position window into a tail-only clip on one card and anchored the
    cap on the other). The bridge's playback history is the witness; when
    it can't vouch either way (no extension), the stamp keeps its old
    trust."""
    appeared = getattr(sentence, "appeared_at", 0.0) or 0.0
    if appeared <= 0.0:
        return None
    if getattr(source, "steady_at", lambda m: None)(
            appeared - timing.APPEAR_LAG) is False:
        if draft is not None:
            draft.notes.append(
                "the row was already on screen after a pause/seek — its "
                "appearance can't anchor the clip")
        return None
    t = getattr(source, "video_time_at", lambda m: None)(
        appeared - timing.APPEAR_LAG)
    if t is None or (near_t is not None
                     and t > near_t + APPEAR_PAST_CLICK_TOL):
        return None
    return t


def _onscreen_match(sentence, source, near_t, t_appear):
    """A window built purely from the clicked row's on-screen life, for when
    the caption track offers NOTHING trustworthy near the click. card_0080:
    the track was ready (green dot) but its ASR had a hole wider than
    window_at's reach right at the click, so text and position matches both
    came up empty — and the card lost its audio to a silent loopback even
    though the source audio and the bridge's time mapping were both sitting
    right there. Requires a click position (the stamp was sanity-checked
    against it in _appearance). Same dict shape as the Transcript windows,
    by='onscreen'."""
    if near_t is None or t_appear is None:
        return None
    cleared = getattr(sentence, "cleared_at", 0.0) or 0.0
    t_end = None
    if cleared > 0.0:
        t_end = getattr(source, "video_time_at", lambda m: None)(
            cleared - timing.CLEAR_LAG)
    if t_end is None:
        t_end = _live_end(source, near_t)
    start = max(0.0, t_appear - APPEAR_BACKSHIFT)
    end = max(t_end, t_appear + PAUSE_TAIL)
    return {"start": start, "end": end, "score": 0.0, "text": "",
            "by": "onscreen", "t_appear": t_appear}


def choose_window(text_match, pos_match, near_t, auto=False, has_life=False):
    """Decide which caption window to trust. The row's OBSERVED on-screen
    life outranks anything an AUTO track says (user call, 2026-07-07: the
    start/end you SAW is the priority, ASR only the fallback — it kept
    picking neighbor lines and had 20s holes at the exact clicks, cards
    0077/0080/0082). So with a mapped appearance (has_life), a text match
    wins only from a HUMAN-made track, strong and near — precision plus the
    OCR-snap ground truth; auto matches yield to the position/on-screen
    machinery. WITHOUT a mapped life the track is all there is: a strong
    text match near the click, then a weaker one that overlaps the position
    window (the two agree on the moment, and the text match spans the
    on-screen SENTENCE where the position window is just the speech chunk
    around the click — card_0044), then the position window alone. Returning
    None hands the choice to the caller's on-screen fallback."""
    if near_t is None:
        return text_match
    if text_match is not None:
        inside = text_match["start"] - SOURCE_POS_TOL <= near_t <= (
            text_match["end"] + SOURCE_POS_TOL)
        strong = text_match.get("score", 0.0) >= SOURCE_STRONG_SCORE
        if inside and strong and not auto:
            return text_match
        if not has_life:
            if inside and strong:
                return text_match
            if pos_match is not None and (
                    text_match["start"] < pos_match["end"]
                    and pos_match["start"] < text_match["end"]):
                return text_match
    if pos_match is not None:
        return pos_match
    # A SEEN life must not be overridden by a leftover auto-track text match
    # that the position search couldn't even corroborate.
    return None if (has_life and auto) else text_match


def _write_from_source(draft, sentence, source, near_t=None, recorder=None):
    """Cut the card's audio with the caption track's timing.

    Playback position is the boss: the text search is confined to captions near
    `near_t`, and a text match is used only when it is strong and lands close to
    where you are. Otherwise the position window (the line playing at `near_t`)
    is used -- correct timing even when the auto-caption text is garbled, which
    is what put earlier cards in the wrong part of the video.

    The clip itself prefers the downloaded source audio (waiting briefly for an
    in-flight download / retrying a failed one); if that's still unavailable,
    the caption window is mapped to clock time and cut from the LOOPBACK buffer
    instead -- the line just played through the speakers, so it's in there.
    Returns True on success; False falls back to OCR-timed loopback, and every
    skip leaves a note so a degraded card is never silent about why."""
    if not getattr(source, "transcript_ready", True):
        status = getattr(source, "status", "") or "idle"
        draft.notes.append("caption track unavailable (yt: %s)" % status)
        return False
    # Before any window is chosen: a row clicked mid-life may be about to
    # vanish — its clear stamp is the truest clip end there is.
    _await_clear(sentence, source)
    text = getattr(sentence, "text", "") or ""
    try:
        text_match = source.window_for(text, near_t=near_t) if text else None
    except Exception as exc:
        draft.notes.append("caption match failed: %s" % exc)
        text_match = None
    pos_match = source.window_at(near_t) if near_t is not None else None

    meta = {}
    try:
        meta = source.meta()
    except Exception:
        pass
    t_appear = _appearance(sentence, source, near_t, draft)
    match = choose_window(text_match, pos_match, near_t,
                          auto=bool(meta.get("caption_auto")),
                          has_life=t_appear is not None)
    if not match:
        match = _onscreen_match(sentence, source, near_t, t_appear)
    if not match:
        draft.notes.append(
            "caption track: no match for this line"
            + ("" if near_t is not None else " (no browser position)"))
        return False

    matched_by = match.get("by", "text")
    draft.source_meta = {
        "video_id": meta.get("video_id"),
        "url": meta.get("url"),
        "title": meta.get("title"),
        "channel": meta.get("channel"),
        "caption_lang": meta.get("caption_lang"),
        "caption_auto": meta.get("caption_auto"),
        "matched_by": matched_by,
        "match_score": round(match.get("score", 0.0), 3),
        "caption_text": match.get("text", ""),
    }

    # A one-word caption can be a fraction of a second: widen the window so
    # the finished clip (window + pre/postroll) is never under MIN_CLIP. And
    # a long cue is cut down to MAX_CLIP around the click position (near_t) —
    # the cue's midpoint without one — so the clicked word stays inside.
    # Bounds read through the module: the settings sliders retune them live.
    start, end = widen_to_min(
        match["start"], match["end"],
        timing.MIN_CLIP - SOURCE_PREROLL - SOURCE_POSTROLL, floor=0.0)
    cap = timing.max_clip() - SOURCE_PREROLL - SOURCE_POSTROLL
    # A POSITION match knows the surrounding speech run but not the word's
    # moment inside it, and near_t is wherever playback sits at card time —
    # often paused PAST the line, which anchored the cap at the sentence's
    # tail and cut its start off (card_0061). The clicked ROW's on-screen
    # appearance is the best anchor there is: hardsub rows stack in sync
    # with speech, so the row carrying the clicked word appeared just as
    # its words were being spoken. That stamp marks a moment to open the
    # window AROUND (with APPEAR_LEAD behind it — stamps run late, and the
    # row's speech begins just before), never a moment to open a forward
    # window FROM: card_0069's clip did that and held only the NEXT rows'
    # audio, the clicked word already over. A stamp mapped later than the
    # user's pause is an artifact (see APPEAR_PAST_CLICK_TOL) and the
    # click position anchors instead.
    center = near_t
    if matched_by == "onscreen":
        # The window IS the row's on-screen life (built in _onscreen_match);
        # cap around its midpoint — nothing is known about the word's moment
        # inside the sentence.
        center = None
        draft.source_meta["anchored_at_appearance"] = round(
            match["t_appear"], 3)
        draft.source_meta["onscreen_appeared"] = round(match["t_appear"], 3)
        draft.source_meta["onscreen_end"] = round(match["end"], 3)
        draft.notes.append(
            "caption track has no text for this line — audio cut from "
            "its on-screen timing")
    elif matched_by == "position":
        if t_appear is not None:
            draft.source_meta["onscreen_appeared"] = round(t_appear, 3)
        # The sentence's on-screen life bounds the clip's END: the clear
        # (mapped to video time) or, for a line still up at click time,
        # just past the click — the words spoken SO FAR are the sentence;
        # what follows is the next line's audio (card_0069/0071 clips were
        # full of it).
        cleared = getattr(sentence, "cleared_at", 0.0) or 0.0
        t_end = None
        if cleared > 0.0:
            t_end = getattr(source, "video_time_at", lambda m: None)(
                cleared - timing.CLEAR_LAG)
            if t_end is not None:
                draft.source_meta["onscreen_cleared"] = round(t_end, 3)
        if t_end is None and near_t is not None:
            t_end = _live_end(source, near_t)
        # _appearance already rejected stamps mapping past the click; the
        # remaining sanity check is against this window's own span.
        plausible = t_appear is not None and start - 3.0 <= t_appear
        if plausible and t_appear > end:
            # The row APPEARED after the position window's last word ended:
            # the track never heard this sentence at all (card_0077 — the
            # ASR skipped the line, a neighbor's phantom end swallowed its
            # span, and every track window nearby is some OTHER line's
            # speech). The row's on-screen life is the only timing that
            # exists for it: open a stamp-lag behind the appearance, close
            # at the clear/click bound, and cap around the life's midpoint
            # — nothing is known about the word's place in the sentence.
            matched_by = "onscreen"
            draft.source_meta["matched_by"] = matched_by
            draft.source_meta["anchored_at_appearance"] = round(t_appear, 3)
            start = max(0.0, t_appear - APPEAR_BACKSHIFT)
            end = t_end if (t_end is not None and t_end > t_appear) else (
                t_appear + PAUSE_TAIL)
            draft.source_meta["onscreen_end"] = round(end, 3)
            start, end = widen_to_min(
                start, end,
                timing.MIN_CLIP - SOURCE_PREROLL - SOURCE_POSTROLL, floor=0.0)
            center = None
            draft.notes.append(
                "caption track has no text for this line — audio cut from "
                "its on-screen timing")
        elif plausible:
            center = t_appear - APPEAR_BACKSHIFT
            draft.source_meta["anchored_at_appearance"] = round(t_appear, 3)
            # The seen start BOUNDS the window, not just centres the cap:
            # an ASR run-on walks the position window back into the
            # PREVIOUS sentences, and a window already at cap length
            # ignores the centre entirely (cards 3/5: 'started too early
            # by a lot' — 4s of the neighbours' speech on the card).
            start = max(start, t_appear - APPEAR_BACKSHIFT - APPEAR_TRIM_GRACE)
            if t_end is not None and start + 0.8 < t_end < end:
                end = t_end
                draft.source_meta["onscreen_end"] = round(t_end, 3)
    if near_t is not None:
        draft.source_meta["click_position"] = round(near_t, 3)
    start, end = shrink_to_max(start, end, cap, center=center)

    wav_path = os.path.join(draft.folder_path, "audio.wav")
    window = {
        "source": "caption_track",
        "matched_by": matched_by,
        "start": start,
        "end": end,
        "preroll": SOURCE_PREROLL,
        "postroll": SOURCE_POSTROLL,
        "score": draft.source_meta["match_score"],
        "caption_text": match.get("text", ""),
        "lang": meta.get("caption_lang"),
        "auto": meta.get("caption_auto"),
    }

    # First choice: the downloaded source audio. ensure_audio waits briefly for
    # an in-flight download and retries a previously failed one.
    ensure = getattr(source, "ensure_audio", None)
    if ensure is not None:
        try:
            ensure()
        except Exception:
            pass
    try:
        secs = source.clip_wav(wav_path, start, end,
                               preroll=SOURCE_PREROLL, postroll=SOURCE_POSTROLL)
    except Exception as exc:
        draft.notes.append("source audio unavailable: %s" % exc)
        secs = 0.0
    if secs > 0.0:
        draft.audio_path = wav_path
        draft.audio_seconds = secs
        draft.audio_window = window
        return True

    # Rescue: same (widened) caption window, cut from the loopback buffer at
    # the clock times the line actually played -- it just left the speakers.
    secs = _loopback_rescue(draft, source, recorder, window, wav_path)
    if secs > 0.0:
        window["source"] = "loopback_caption_timed"
        draft.audio_window = window
        if not _drop_if_silent(draft, recorder, wav_path):
            draft.audio_path = wav_path
            draft.audio_seconds = secs
            draft.notes.append("audio cut from loopback with caption timing "
                               "(source download unavailable)")
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
        return recorder.save_wav(wav_path, t0 - SOURCE_PREROLL,
                                 t1 + SOURCE_POSTROLL)
    except Exception as exc:
        draft.notes.append("loopback rescue failed: %s" % exc)
        return 0.0
