"""Build card draft folders from clicked OCR words.

The audio half (window choice + cutting) lives in clip.py; this module owns
what the CARD says — text, sentence provenance, translations, the
click-time screenshot — and the draft's assembly order."""

import os
import time
from difflib import SequenceMatcher

from ..detection.sentence import CaptionBlock, caption_block
from ..language import grammar, pronounce
from ..language import translate as translate_mod
from ..language.dictionary import meaning
from ..language.translate import TranslationError, clean_word, translate
from . import prefs, timing
from .clip import SOURCE_STRONG_SCORE, write_audio
from .model import CardDraft
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

# Sentence completion (word-at-a-time hardsubs): how far a transcript row's
# stamps may sit from a track word's start and still count as OUR sighting
# of that word — appear/clear stamps run ~0.3s late (detection.APPEAR_LAG).
FILL_SLACK = 0.45
# Two logged rows of the same text within this are one sighting (a re-read),
# not two words.
FILL_DEDUP = 0.5
# A row claims a track word only when one of its own words is at least this
# character-similar — loose enough for OCR misreads ('pbyed' ~ 'played'),
# tight enough that a watermark never claims a real word.
FILL_TEXT_SIM = 0.5


def capture_png(region):
    """PNG bytes for the tracked area (physical left, top, width, height)."""
    import mss
    import mss.tools

    left, top, width, height = region
    with mss.mss() as sct:
        shot = sct.grab({
            "left": int(left),
            "top": int(top),
            "width": int(width),
            "height": int(height),
        })
    return mss.tools.to_png(shot.rgb, shot.size)


def write_region_png(region, path):
    """Capture the tracked area and write it to path as PNG."""
    write_png_bytes(path, capture_png(region))


def write_png_bytes(path, data):
    with open(path, "wb") as f:
        f.write(data)


def _box_tuple(value):
    if not value:
        return None
    try:
        return tuple(int(v) for v in value)
    except Exception:
        return None


def _box_contains(outer, inner, slack=3):
    if outer is None or inner is None:
        return False
    return (inner[0] >= outer[0] - slack
            and inner[1] >= outer[1] - slack
            and inner[2] <= outer[2] + slack
            and inner[3] <= outer[3] + slack)


def attach_sentence_provenance(word, draft, sentence=None):
    """Record whether the clicked Word belongs to the saved OCR sentence.
    `sentence` is what the card actually saves — the word's own line, or the
    joined CaptionBlock when the caption spanned several lines; it defaults
    to the word's line."""
    if sentence is None:
        sentence = getattr(word, "sentence", None)
    draft.word_box = _box_tuple(getattr(word, "box", None))
    draft.sentence_box = _box_tuple(getattr(sentence, "box", None))
    words = list(getattr(sentence, "words", []) or [])

    # A clicked Word is normally one of the sentence's own. On CJK lines it
    # is not: the hotspots are per character and the clicked word was fused
    # out of a character RANGE (sentence.span_word), so it is matched by the
    # line it came from plus the offset it starts at — which is its first
    # character's hotspot, and lands on the same index the old identity test
    # would have given.
    identity_index = -1
    for i, candidate in enumerate(words):
        if candidate is word:
            identity_index = i
            break
    if identity_index < 0 and getattr(word, "index", -1) >= 0:
        for i, candidate in enumerate(words):
            if (getattr(candidate, "sentence", None)
                    is getattr(word, "sentence", None)
                    and getattr(candidate, "index", -1) == word.index):
                identity_index = i
                break
    draft.word_index = identity_index

    if sentence is None:
        draft.notes.append("sentence missing from clicked word")
        return
    if identity_index < 0:
        draft.notes.append("clicked word is not in its sentence word list")
    if not _box_contains(draft.sentence_box, draft.word_box):
        draft.notes.append("clicked word box is outside the sentence box")

    cleaned = clean_word(getattr(word, "text", "")) or getattr(word, "text", "")
    if cleaned and cleaned.casefold() not in (draft.sentence or "").casefold():
        draft.notes.append("clicked word text not found in OCR sentence")

    draft.sentence_verified = (
        identity_index >= 0
        and _box_contains(draft.sentence_box, draft.word_box)
    )


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
    # The card studies the DICTIONARY form when the lookup found one: the
    # screen said 戻って, the word to learn is 戻る. The surface is kept for
    # the provenance check (it, not the lemma, is what is in the sentence)
    # and recorded on the card.
    surface = getattr(word, "text", "") or ""
    raw_word = getattr(word, "lemma", None) or surface
    draft = CardDraft(clean_word(raw_word) or raw_word,
                      getattr(sentence, "text", "") or "")
    cleaned_surface = clean_word(surface) or surface
    if cleaned_surface != draft.word:
        draft.word_surface = cleaned_surface
    draft.appeared_at = getattr(sentence, "appeared_at", 0.0)
    draft.cleared_at = getattr(sentence, "cleared_at", 0.0)
    draft.folder_path = next_card_dir(out_dir)
    attach_sentence_provenance(word, draft, sentence)
    conf = getattr(sentence, "ocr_conf", None)
    if conf is not None and conf < OCR_SHAKY_CONF:
        draft.notes.append(
            "OCR was unsure of this text (confidence %.2f) — the word and "
            "sentence are probably misread" % conf)

    # Word-at-a-time hardsubs: the clicked "sentence" may be one chunk of a
    # longer spoken sentence — complete it BEFORE translation and audio, so
    # both see the real sentence (fail-soft; leaves other captions alone).
    if source is not None and draft.sentence:
        _complete_sentence(draft, sentence, source, near_t)

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
    _translate_fields(draft, translator)
    # The word's anatomy (from the on-screen surface + lemma, exactly as the
    # popup's Grammar tab) and a TTS reading of the studied headword. Gathered
    # AFTER _snap_to_track so the spoken word is the caption-corrected one. Off
    # in the settings -> not gathered at all (no note, per prefs' contract).
    if prefs.include("breakdown"):
        _write_breakdown(draft, word)
    if prefs.include("word_audio"):
        _write_word_audio(draft)
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
                # Audit only: the row's FULL clear, if it vanished after the
                # audio was already cut. The clip's own end is end_seconds.
                draft.source_meta["final_onscreen_cleared"] = round(t_clear, 3)
    write_artifacts(draft)
    return draft


def _norm_word(text):
    return (clean_word(text) or text or "").casefold()


def _boxes_overlap(ref, other):
    """Do two (l, t, r, b) rectangles share any area? True when `other` is
    unknown (None / malformed) — the spatial gate only ever EXCLUDES on a
    confirmed non-overlap, never on missing information (user rule)."""
    if not other or not ref or len(other) < 4 or len(ref) < 4:
        return True
    al, at, ar, ab = ref[:4]
    bl, bt, br, bb = other[:4]
    return al < br and bl < ar and at < bb and bt < ab


def _fill_sentence(rows, sent, low_conf=OCR_SHAKY_CONF):
    """Merge OUR transcript rows with the track SENTENCE they sit in — the
    word-at-a-time fix: our transcript's words are
    always taken first; the track only reveals that the sentence extends
    beyond what OCR caught and fills the words OCR missed; a low-confidence
    row loses its say and the track speaks there instead. Timing likewise:
    ours where a row exists at an edge, the track's otherwise.

    rows: transcript records (text/appeared_video/cleared_video/ocr_conf).
    sent: Transcript.sentence_for's dict. Returns {text, start, end,
    filled} or None when there is nothing to merge."""
    kept = []
    for r in rows:
        conf = r.get("ocr_conf")
        if conf is not None and conf < low_conf:
            continue                      # unsure read: the track speaks here
        if any(_norm_word(r["text"]) == _norm_word(k["text"])
               and abs(r["appeared_video"] - k["appeared_video"]) < FILL_DEDUP
               for k in kept):
            continue                      # a re-read of the same sighting
        kept.append(r)
    if not kept:
        return None                       # nothing of OURS: don't build a
                                          # pure-ASR sentence
    kept.sort(key=lambda k: k["appeared_video"])

    # Which track words did OUR rows actually say? A row claims a word only
    # when it is BOTH time-near (within slack of the row's span) and
    # text-similar to one of the row's own words, at most as many words as
    # the row holds, nearest first. Both legs matter: time alone let a
    # one-word row swallow its neighbour's word, and let a watermark row
    # ('COMEDY PANTHEON' spans the whole sentence) claim real words; text
    # alone would match a repeated word minutes away.
    words = sent["words"]
    claimed = set()
    for k in kept:
        a, c = k["appeared_video"], k["cleared_video"]
        own = [_norm_word(x) for x in k["text"].split() if _norm_word(x)]
        k["own_n"] = len(own)
        cand = []
        for i, w in enumerate(words):
            if i in claimed or not (a - FILL_SLACK <= w[1]
                                    <= c + FILL_SLACK):
                continue
            tw = _norm_word(w[0])
            if not tw or not own:
                continue
            if max(SequenceMatcher(None, tw, x).ratio()
                   for x in own) < FILL_TEXT_SIM:
                continue
            cand.append((max(0.0, a - w[1], w[1] - c), i))
        k["claimed_idx"] = [i for _, i in sorted(cand)[:len(own)]]
        claimed.update(k["claimed_idx"])

    # A row belongs to this sentence only when MOST of its own words line up
    # with the track — the sentence-level bar, not a single lucky word. A
    # real caption line matches a run of the track; a stray row that merely
    # shares one word does not (card_0031: the video TITLE 'Watch Over And
    # Over Again', five words, matched only the spoken 'what' at 0.67 and
    # spliced itself into the middle of the sentence). A one/two-word row
    # still needs its one word; a five-word row needs three. This also drops
    # a row that said NO word of the sentence at all (a neighbour's line,
    # chrome the track never heard — 'Inter Miami' on the g4erZWrKEjQ data).
    kept = [k for k in kept
            if len(k["claimed_idx"]) >= max(1, (k["own_n"] + 1) // 2)]
    if not kept:
        return None
    # Rebuild the claimed set from the SURVIVORS only: a rejected row (the
    # title) must not keep the track words it falsely grabbed out of the fill
    # — otherwise the spoken 'what' it stole would vanish from the sentence
    # instead of being filled back in from the track.
    claimed = set()
    for k in kept:
        claimed.update(k["claimed_idx"])

    # One time-ordered stream: our rows, plus only the track words no row
    # of ours claimed. (t, tiebreak, text, t_end) — rows sort before a track
    # word at the same instant.
    events = [(k["appeared_video"], 0, k["text"], k["cleared_video"])
              for k in kept]
    filled = [w for i, w in enumerate(words) if i not in claimed]
    events += [(w[1], 1, w[0], w[2]) for w in filled]
    events.sort(key=lambda e: (e[0], e[1]))
    return {
        "text": " ".join(e[2] for e in events),
        "start": events[0][0],
        "end": max(e[3] for e in events),
        "filled": len(filled),
    }


def _complete_sentence(draft, sentence, source, near_t):
    """A word-at-a-time hardsub shows one caption chunk while the SENTENCE
    spans many (the transcript for g4erZWrKEjQ caught 'RESPECT' alone while
    'I don't respect you at all' was being spoken). Find the track sentence
    containing the clicked text (the text match is the gate: burned-in
    translation subs don't match and are left alone), then rebuild the full
    sentence — our rows first, track filling the gaps — and hand the span
    to the audio cut. Fail-soft: any miss leaves the card exactly as it
    was."""
    sent = getattr(source, "sentence_for", lambda *a, **k: None)(
        draft.sentence, near_t)
    if not sent:
        return
    rows = getattr(source, "rows_between", lambda *a: [])(
        sent["start"] - 0.75, sent["end"] + 0.75)
    # Spatial gate (user rule): a row is part of the clicked sentence only if
    # its on-screen box OVERLAPS the clicked caption's — a caption elsewhere
    # on screen (the title band up top, a watermark) is definitely not part
    # of it, no matter how the words happen to line up. One-directional: no
    # overlap EXCLUDES; overlap alone doesn't include (the text tests in
    # _fill_sentence still decide). An unknown box on either side fails open,
    # so older logs and mapping gaps keep their old behaviour.
    ref_box = getattr(sentence, "box", None)
    if ref_box is not None:
        rows = [r for r in rows if _boxes_overlap(ref_box, r.get("box"))]
    # The clicked block itself may still be on screen (not yet in the log):
    # stand it in as a row at its matched spot so OUR read of it wins.
    block_row = {"text": draft.sentence,
                 "appeared_video": sent["match_start"],
                 "cleared_video": sent["match_end"],
                 "ocr_conf": getattr(sentence, "ocr_conf", None)}
    got = _fill_sentence([block_row] + list(rows), sent)
    if not got:
        return
    old_words = [_norm_word(w) for w in draft.sentence.split()]
    new_words = [_norm_word(w) for w in got["text"].split()]
    if new_words == old_words:
        return                            # the block already IS the sentence
    draft.assembled = {
        "start": round(got["start"], 3),
        "end": round(got["end"], 3),
        "score": round(sent["score"], 3),
        "ocr_sentence": draft.sentence,
        "filled_from_track": got["filled"],
    }
    draft.sentence = got["text"]
    target = _norm_word(draft.word)
    draft.word_index = next(
        (i for i, w in enumerate(new_words) if w == target), -1)
    draft.notes.append(
        "sentence completed from this run's transcript + caption track "
        "(%d word(s) filled from the track)" % got["filled"])


def _snap_to_track(draft):
    """Correct OCR misreads from the caption track (card_0018: a punctuation
    glyph read as an alif turned معروف into معروفا and poisoned the word's
    translation). Only from a HUMAN-MADE track — auto captions are speech
    recognition, often wrong, and must never rewrite a burned-in subtitle a
    person wrote. Only when the track's text match is STRONG — then the
    track's words are what the caption really said (the match is recorded
    as provenance; it never times the clip) — and only for OCR words that
    ALMOST equal their aligned track word; dissimilar pairs are left
    alone."""
    win = draft.audio_window or {}
    if (win.get("auto")
            or not win.get("caption_text")
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


def _translate_fields(draft, translator):
    if prefs.include("word_translation"):
        try:
            # Dictionary definitions when Wiktionary knows the word; the
            # contextual translation (this `translator`) as hint + fallback.
            draft.word_translation = meaning(draft.word, draft.sentence,
                                             translate_fn=translator)
        except TranslationError as exc:
            draft.notes.append("word translation: %s" % exc)

    if draft.sentence and prefs.include("sentence_translation"):
        # Translate the sentence FLAT (space-joined, exactly as the card
        # displays it): caption wraps break for WIDTH, not clauses — the
        # comma join tried for card_0074 garbled mid-clause wraps ("the
        # cat, I gave birth").
        try:
            draft.sentence_translation = translator(draft.sentence)
        except TranslationError as exc:
            draft.notes.append("sentence translation: %s" % exc)


def _write_breakdown(draft, word):
    """Fill the Breakdown field with the word's anatomy — the SAME rich text
    the popup's Grammar tab shows (language/grammar.anatomy_html), read off the
    on-screen surface and the resolved lemma. Fail-soft: any lookup trouble is
    a note, never a raise."""
    surface = clean_word(getattr(word, "text", "")) or getattr(word, "text", "")
    lemma = getattr(word, "lemma", None)
    try:
        draft.breakdown = grammar.anatomy_html(surface, lemma) or ""
    except Exception as exc:
        draft.notes.append("breakdown lookup failed: %s" % exc)


def _write_word_audio(draft):
    """Fetch a TTS reading of the studied headword into word_audio.mp3 — the
    same free Google endpoint the popup's 🔊 uses (pronounce.fetch, no LLM).
    Auto/blank language cannot be spoken (the endpoint needs a named voice);
    every trouble is a note, never a raise."""
    text = draft.word
    lang = translate_mod.SOURCE_LANGUAGE
    if not text or lang in ("", "auto"):
        return
    try:
        data = pronounce.fetch(text, lang)
    except pronounce.PronounceError as exc:
        draft.notes.append("word audio: %s" % exc)
        return
    except Exception as exc:
        draft.notes.append("word audio failed: %s" % exc)
        return
    path = os.path.join(draft.folder_path, "word_audio.mp3")
    try:
        with open(path, "wb") as f:
            f.write(data)
        draft.word_audio_path = path
    except OSError as exc:
        draft.notes.append("word audio save failed: %s" % exc)


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

