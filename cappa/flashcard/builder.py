"""Build card draft folders from clicked OCR words."""

import os
import time

from ..translate import TranslationError, clean_word, translate
from .model import CardDraft
from .provenance import attach_sentence_provenance
from .screenshot import write_png_bytes, write_region_png
from .timing import (SOURCE_POSTROLL, SOURCE_PREROLL, audio_window)
from .writer import CARDS_DIR, next_card_dir, write_artifacts

# A text match is trusted over the raw playback position only when it is both
# strong and lands near where we actually are -- otherwise a wrong auto-caption
# nearby could still shift the clip. Below that, the position window wins.
SOURCE_STRONG_SCORE = 0.75
SOURCE_POS_TOL = 6.0   # seconds a trusted text match may sit from near_t


def build_draft(word, region, recorder, out_dir=CARDS_DIR, translator=translate,
                screenshotter=write_region_png, screenshot_png=None,
                screenshot_note="", source=None, near_t=None):
    """Gather one card's ingredients into a draft folder.

    region is the tracked area as physical (left, top, width, height) or None.
    recorder is a LoopbackRecorder or None. source is a SourceSession (the
    active YouTube video) or None: when it can align this line to a caption
    track, the card's audio comes from there instead of the loopback buffer.
    near_t is the playback position (seconds) captured at click time from the
    browser bridge; it both constrains the caption text search to where you are
    and provides a language-neutral position window fallback. Missing pieces are
    recorded in draft.notes instead of raising.
    """
    now = time.monotonic()
    sentence = getattr(word, "sentence", None)
    raw_word = getattr(word, "text", "") or ""
    draft = CardDraft(clean_word(raw_word) or raw_word,
                      getattr(sentence, "text", "") or "")
    draft.appeared_at = getattr(sentence, "appeared_at", 0.0)
    draft.cleared_at = getattr(sentence, "cleared_at", 0.0)
    draft.folder_path = next_card_dir(out_dir)
    attach_sentence_provenance(word, draft)

    _translate_fields(draft, translator)
    _write_screenshot(draft, region, screenshotter, screenshot_png,
                      screenshot_note)
    _write_audio(draft, sentence, recorder, now, source, near_t)
    write_artifacts(draft)
    return draft


def _translate_fields(draft, translator):
    try:
        draft.word_translation = translator(draft.word)
    except TranslationError as exc:
        draft.notes.append("word translation: %s" % exc)

    if draft.sentence:
        try:
            draft.sentence_translation = translator(draft.sentence)
        except TranslationError as exc:
            draft.notes.append("sentence translation: %s" % exc)


def _write_screenshot(draft, region, screenshotter, screenshot_png,
                      screenshot_note):
    if screenshot_png is not None:
        img_path = os.path.join(draft.folder_path, "screenshot.png")
        try:
            write_png_bytes(img_path, screenshot_png)
            draft.image_path = img_path
            draft.screenshot_source = "word_click"
        except Exception as exc:
            draft.notes.append("screenshot failed: %s" % exc)
        return

    if screenshot_note:
        draft.notes.append(screenshot_note)
        return

    if region is None:
        draft.notes.append("no tracked area for a screenshot")
        return

    img_path = os.path.join(draft.folder_path, "screenshot.png")
    try:
        screenshotter(region, img_path)
        draft.image_path = img_path
        draft.screenshot_source = "create_card"
    except Exception as exc:
        draft.notes.append("screenshot failed: %s" % exc)


def _choose_window(text_match, pos_match, near_t):
    """Decide which caption window to trust. With a playback position: take a
    text match only if it's strong and temporally consistent with where we are,
    else the position window. Without one: the best text match is all we have."""
    if near_t is None:
        return text_match
    if text_match is not None and text_match.get("score", 0.0) >= SOURCE_STRONG_SCORE:
        inside = text_match["start"] - SOURCE_POS_TOL <= near_t <= (
            text_match["end"] + SOURCE_POS_TOL)
        if inside:
            return text_match
    return pos_match or text_match


def _write_audio_from_source(draft, sentence, source, near_t=None,
                             recorder=None):
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
    text = getattr(sentence, "text", "") or ""
    try:
        text_match = source.window_for(text, near_t=near_t) if text else None
    except Exception as exc:
        draft.notes.append("caption match failed: %s" % exc)
        text_match = None
    pos_match = source.window_at(near_t) if near_t is not None else None

    match = _choose_window(text_match, pos_match, near_t)
    if not match:
        draft.notes.append(
            "caption track: no match for this line"
            + ("" if near_t is not None else " (no browser position)"))
        return False

    meta = {}
    try:
        meta = source.meta()
    except Exception:
        pass
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

    wav_path = os.path.join(draft.folder_path, "audio.wav")
    window = {
        "source": "caption_track",
        "matched_by": matched_by,
        "start": match["start"],
        "end": match["end"],
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
        secs = source.clip_wav(wav_path, match["start"], match["end"],
                               preroll=SOURCE_PREROLL, postroll=SOURCE_POSTROLL)
    except Exception as exc:
        draft.notes.append("source audio unavailable: %s" % exc)
        secs = 0.0
    if secs > 0.0:
        draft.audio_path = wav_path
        draft.audio_seconds = secs
        draft.audio_window = window
        return True

    # Rescue: same caption window, cut from the loopback buffer at the clock
    # times the line actually played -- it just came out of the speakers.
    secs = _loopback_rescue(draft, source, recorder, match, wav_path)
    if secs > 0.0:
        window["source"] = "loopback_caption_timed"
        draft.audio_path = wav_path
        draft.audio_seconds = secs
        draft.audio_window = window
        draft.notes.append("audio cut from loopback with caption timing "
                           "(source download unavailable)")
        return True
    return False


def _loopback_rescue(draft, source, recorder, match, wav_path):
    """Cut the matched caption window from the loopback ring buffer via the
    bridge's video-time -> clock-time mapping. 0.0 when impossible."""
    if recorder is None or not getattr(recorder, "ready", False):
        return 0.0
    mono = getattr(source, "monotonic_window", None)
    if mono is None:
        return 0.0
    window = mono(match["start"], match["end"])
    if window is None:
        return 0.0
    t0, t1 = window
    try:
        return recorder.save_wav(wav_path, t0 - SOURCE_PREROLL,
                                 t1 + SOURCE_POSTROLL)
    except Exception as exc:
        draft.notes.append("loopback rescue failed: %s" % exc)
        return 0.0


def _write_audio(draft, sentence, recorder, now, source=None, near_t=None):
    # Preferred: caption-track timing (source audio, or loopback mapped to the
    # caption's clock time). Falls through to OCR-timed loopback when there's
    # no source, no match, or no audio anywhere.
    if source is not None and _write_audio_from_source(
            draft, sentence, source, near_t, recorder):
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
        draft.audio_path = wav_path
        draft.audio_seconds = secs
    else:
        draft.notes.append("no audio in the buffer for that window")
