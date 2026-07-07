"""Build card draft folders from clicked OCR words.

The audio half (window choice + cutting) lives in clip.py; this module owns
what the CARD says — text, provenance, translations, screenshot — and the
draft's assembly order."""

import os
import time
from difflib import SequenceMatcher

from ..detection.sentence import CaptionBlock, caption_block
from ..dictionary import meaning
from ..translate import TranslationError, clean_word, translate
from . import prefs, timing
from .clip import SOURCE_STRONG_SCORE, write_audio
from .model import CardDraft
from .provenance import attach_sentence_provenance
from .screenshot import write_png_bytes, write_region_png
from .writer import CARDS_DIR, next_card_dir, write_artifacts

# An OCR word is snapped to its aligned caption-track word only when the two
# are ALREADY this character-similar: fixes misreads (a punctuation glyph
# read as a letter, one confused character) without letting a legitimately
# different auto-caption word rewrite a correct read.
TRACK_SNAP_SIM = 0.66

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
        write_audio(draft, sentence, recorder, now, source, near_t)
        _snap_to_track(draft)
    _translate_fields(draft, translator, sentence)
    # A row clicked mid-life often clears while the card is still building
    # (translations take a beat): refresh the stamp so the metadata records
    # the row's FULL on-screen life, not the life as of the click. The audio
    # is already cut by now — this is the audit trail (card_0077: "the line
    # lived 13:52-13:55" must be checkable against the saved numbers).
    cleared = getattr(sentence, "cleared_at", 0.0) or 0.0
    if cleared > draft.cleared_at:
        draft.cleared_at = cleared
        if source is not None and draft.source_meta is not None:
            t_clear = getattr(source, "video_time_at", lambda m: None)(
                cleared - timing.CLEAR_LAG)
            if t_clear is not None:
                draft.source_meta["onscreen_cleared"] = round(t_clear, 3)
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

