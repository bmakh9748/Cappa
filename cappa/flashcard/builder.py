"""Build card draft folders from clicked OCR words."""

import os
import time

from ..translate import TranslationError, clean_word, translate
from .model import CardDraft
from .provenance import attach_sentence_provenance
from .screenshot import write_png_bytes, write_region_png
from .timing import audio_window
from .writer import CARDS_DIR, next_card_dir, write_artifacts


def build_draft(word, region, recorder, out_dir=CARDS_DIR, translator=translate,
                screenshotter=write_region_png, screenshot_png=None,
                screenshot_note=""):
    """Gather one card's ingredients into a draft folder.

    region is the tracked area as physical (left, top, width, height) or None.
    recorder is a LoopbackRecorder or None. Missing pieces are recorded in
    draft.notes instead of raising.
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
    _write_audio(draft, sentence, recorder, now)
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


def _write_audio(draft, sentence, recorder, now):
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
