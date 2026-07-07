"""Build card draft folders from clicked OCR words."""

import os
import time
from difflib import SequenceMatcher

from ..detection.sentence import CaptionBlock, caption_block
from ..dictionary import meaning
from ..translate import TranslationError, clean_word, translate
from . import prefs, timing
from .model import CardDraft
from .provenance import attach_sentence_provenance
from .screenshot import write_png_bytes, write_region_png
from .timing import (SOURCE_POSTROLL, SOURCE_PREROLL, audio_window,
                     shrink_to_max, widen_to_min)
from .writer import CARDS_DIR, next_card_dir, write_artifacts

# A text match is trusted over the raw playback position only when it is both
# strong and lands near where we actually are -- otherwise a wrong auto-caption
# nearby could still shift the clip. Below that, the position window wins.
SOURCE_STRONG_SCORE = 0.75
SOURCE_POS_TOL = 6.0   # seconds a trusted text match may sit from near_t

# An OCR word is snapped to its aligned caption-track word only when the two
# are ALREADY this character-similar: fixes misreads (a punctuation glyph
# read as a letter, one confused character) without letting a legitimately
# different auto-caption word rewrite a correct read.
TRACK_SNAP_SIM = 0.66

# A loopback clip whose int16 peak is at or under this is silence, not speech
# (card_0027 came out at peak 1 of 32767: the bound endpoint wasn't the one
# playing). Such a clip is DISCARDED — a silent wav on a card is worse than
# no audio — and the note says what happened.
SILENT_CLIP_PEAK = 100

# Below this recognition confidence the card says its text is probably
# misread. Caption reads run 0.9+; card_0060 (a fully-vocalized Arabic
# poetry page, beyond the rec model) read at 0.45-0.52 and still produced a
# confident-looking garbage card.
OCR_SHAKY_CONF = 0.8


def build_draft(word, region, recorder, out_dir=CARDS_DIR, translator=translate,
                screenshotter=write_region_png, screenshot_png=None,
                screenshot_note="", source=None, near_t=None, captions=None):
    """Gather one card's ingredients into a draft folder.

    region is the tracked area as physical (left, top, width, height) or None.
    recorder is a LoopbackRecorder or None. source is a SourceSession (the
    active YouTube video) or None: when it can align this line to a caption
    track, the card's audio comes from there instead of the loopback buffer.
    near_t is the playback position (seconds) captured at click time from the
    browser bridge; it both constrains the caption text search to where you are
    and provides a language-neutral position window fallback. captions is the
    live Sentence list at click time: lines visibly stacked with the clicked
    one join it, so a two-line subtitle lands whole on the card. Missing
    pieces are recorded in draft.notes instead of raising; pieces the user
    turned OFF in the card settings (prefs) are skipped without a note --
    that absence is intentional, not a degradation.
    """
    now = time.monotonic()
    sentence = getattr(word, "sentence", None)
    if sentence is not None and captions:
        lines = caption_block(sentence, captions)
        if len(lines) > 1:
            sentence = CaptionBlock(lines, sentence)
    raw_word = getattr(word, "text", "") or ""
    draft = CardDraft(clean_word(raw_word) or raw_word,
                      getattr(sentence, "text", "") or "")
    draft.appeared_at = getattr(sentence, "appeared_at", 0.0)
    draft.cleared_at = getattr(sentence, "cleared_at", 0.0)
    draft.folder_path = next_card_dir(out_dir)
    attach_sentence_provenance(word, draft, sentence)
    conf = getattr(sentence, "ocr_conf", None)
    if conf is not None and conf < OCR_SHAKY_CONF:
        draft.notes.append(
            "OCR was unsure of this text (confidence %.2f) — the word and "
            "sentence are probably misread" % conf)

    if prefs.include("screenshot"):
        _write_screenshot(draft, region, screenshotter, screenshot_png,
                          screenshot_note)
    # Audio first: a strong caption-track text match doubles as ground truth
    # for what the caption really SAID, so the OCR text is corrected from it
    # before anything gets translated. (Audio off skips the caption match
    # too, so those cards keep their raw OCR text.)
    if prefs.include("audio"):
        _write_audio(draft, sentence, recorder, now, source, near_t)
        _snap_to_track(draft)
    _translate_fields(draft, translator, sentence)
    write_artifacts(draft)
    return draft


def _snap_to_track(draft):
    """Correct OCR misreads from the caption track (card_0018: a punctuation
    glyph read as an alif turned معروف into معروفا and poisoned the word's
    translation). Only from a HUMAN-MADE track — auto captions are speech
    recognition, often wrong, and must never rewrite a burned-in subtitle a
    person wrote. Only when the audio window came from a STRONG text match —
    then the track's words are what the caption really said — and only for
    OCR words that ALMOST equal their aligned track word; dissimilar pairs
    are left alone."""
    win = draft.audio_window or {}
    if (win.get("auto")
            or win.get("matched_by") != "text"
            or win.get("score", 0.0) < SOURCE_STRONG_SCORE):
        return
    ocr_words = (draft.sentence or "").split()
    track_words = (win.get("caption_text") or "").split()
    if not ocr_words or not track_words:
        return

    def norm(w):
        return clean_word(w).casefold()

    a = [norm(w) for w in ocr_words]
    b = [norm(w) for w in track_words]
    fixed = list(ocr_words)
    fixes = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b).get_opcodes():
        # Only one-to-one substitutions are safe: an insert/delete or an
        # uneven block means the line SPLIT differently, not a misread.
        if tag != "replace" or i2 - i1 != j2 - j1:
            continue
        for k in range(i2 - i1):
            i, j = i1 + k, j1 + k
            if not a[i] or not b[j]:
                continue
            if SequenceMatcher(None, a[i], b[j]).ratio() >= TRACK_SNAP_SIM:
                fixed[i] = track_words[j]
                fixes.append((ocr_words[i], track_words[j]))
    if not fixes:
        return

    # The clicked word rides along when it was one of the fixed words.
    wi = draft.word_index
    if not (0 <= wi < len(ocr_words) and norm(ocr_words[wi]) == norm(draft.word)):
        wi = next((i for i, w in enumerate(ocr_words)
                   if norm(w) == norm(draft.word)), -1)
    if wi >= 0 and fixed[wi] != ocr_words[wi]:
        draft.word = clean_word(fixed[wi]) or fixed[wi]

    if draft.source_meta is not None:
        draft.source_meta["ocr_sentence"] = draft.sentence
    draft.sentence = " ".join(fixed)
    for old, new in fixes:
        draft.notes.append("caption track correction: %s -> %s" % (old, new))


def _translate_fields(draft, translator, sentence=None):
    if prefs.include("word_translation"):
        try:
            # Dictionary definitions when Wiktionary knows the word; the
            # contextual translation (this `translator`) as hint + fallback.
            draft.word_translation = meaning(draft.word, draft.sentence,
                                             translate_fn=translator)
        except TranslationError as exc:
            draft.notes.append("word translation: %s" % exc)

    if draft.sentence and prefs.include("sentence_translation"):
        # Screen rows usually break at clause boundaries: joined with
        # commas Google parses them as clauses, where the flat space-join
        # fused them into one garbled parse (card_0074: "kalo pagi pilih
        # kobo" became "If you choose Kobo in the morning"). A mid-clause
        # wrap tolerates the stray comma (card_0031's block translates
        # identically). The card still DISPLAYS the plain-joined sentence.
        lines = getattr(sentence, "lines", None)
        to_translate = (", ".join(s.text for s in lines if s.text)
                        if lines else draft.sentence)
        try:
            draft.sentence_translation = translator(to_translate)
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


# A position-matched clip CENTERS this far before the clicked row's
# appearance stamp. The stamp trails the row's actual speech by a variable
# lag — settle debounce, scan interval, clock-mapping slack — measured on
# the same row watched twice: card_0069 stamped 823.3 and card_0071 stamped
# 824.3 for a row whose cue ran 822.6-823.5. So the clicked word's audio
# lies BEFORE the stamp far more often than after; a window centred a
# second behind it covers both watches, where a forward window missed the
# word entirely both times.
APPEAR_BACKSHIFT = 1.0
# An appearance mapped LATER than where the user paused is impossible for a
# row they were reading — it's a re-read's stamp or a paused-seek mapping
# artifact; past this slack the click position anchors the clip instead.
APPEAR_PAST_CLICK_TOL = 1.0
# Hardsubs leave with their speech: nothing past the sentence's on-screen
# END belongs on the card. A SEEN clear needs no buffer — its stamp already
# trails the real vanish (user call); only the pause path, where the line
# is still up and the true end unknown, gets this small tail past the click.
PAUSE_TAIL = 0.4


def _choose_window(text_match, pos_match, near_t):
    """Decide which caption window to trust. With a playback position: a
    strong, temporally consistent text match wins outright. A WEAKER text
    match still wins when it overlaps the position window — the two agree on
    the moment, and the text match spans the on-screen SENTENCE where the
    position window is just the speech chunk around the click (card_0044
    clipped mid-sentence on a garbled auto-caption). A text match that
    doesn't even overlap where we are is not trusted. Without a position:
    the best text match is all we have."""
    if near_t is None:
        return text_match
    if text_match is not None:
        inside = text_match["start"] - SOURCE_POS_TOL <= near_t <= (
            text_match["end"] + SOURCE_POS_TOL)
        if inside and text_match.get("score", 0.0) >= SOURCE_STRONG_SCORE:
            return text_match
        if pos_match is not None and (text_match["start"] < pos_match["end"]
                                      and pos_match["start"] < text_match["end"]):
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
    if matched_by == "position":
        appeared = getattr(sentence, "appeared_at", 0.0) or 0.0
        t_appear = getattr(source, "video_time_at", lambda m: None)(
            appeared - timing.APPEAR_LAG)
        if (t_appear is not None and start - 3.0 <= t_appear <= end
                and (near_t is None
                     or t_appear <= near_t + APPEAR_PAST_CLICK_TOL)):
            center = t_appear - APPEAR_BACKSHIFT
            draft.source_meta["anchored_at_appearance"] = round(t_appear, 3)
            # The sentence's on-screen life bounds the clip's END: cut at
            # the clear (mapped to video time) or, for a line still up at
            # click time, just past the pause — the words spoken SO FAR are
            # the sentence; what follows is the next line's audio
            # (card_0069/0071 clips were full of it).
            cleared = getattr(sentence, "cleared_at", 0.0) or 0.0
            t_end = None
            if cleared > 0.0:
                t_end = getattr(source, "video_time_at", lambda m: None)(
                    cleared - timing.CLEAR_LAG)
            if t_end is None and near_t is not None:
                t_end = near_t + PAUSE_TAIL
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


def _write_audio(draft, sentence, recorder, now, source=None, near_t=None):
    # Preferred: caption-track timing (source audio, or loopback mapped to the
    # caption's clock time). Falls through to OCR-timed loopback when there's
    # no source, no match, or no audio anywhere.
    if source is not None and _write_audio_from_source(
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
