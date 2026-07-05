"""Assemble a flashcard draft from a clicked word.

On click we already have the Word, and through it the Sentence it sits in
plus that sentence's on-screen appear/clear timestamps. build_draft() turns
that into a CardDraft: the word and its translation, the full sentence and
its translation, a PNG screenshot of the tracked area, and - the hard part -
a WAV of the audio that played while the sentence was on screen, cut from the
recorder's rolling buffer.

No .apkg is produced here. Each click creates a folder under cards/ containing
the draft pieces plus metadata.json, so the export step can inspect and bundle
them without relying on console output. build_draft() blocks (two network
translations + a screen grab + a WAV write), so callers run it off the UI
thread - the popup does, on its own helper thread.

Timing: WHY the clip isn't just [appeared_at, cleared_at]: our detection lags
the real screen. A line is boxed up to a scan-interval + settle after it
actually appears, and a clear is only confirmed after a debounce. So the true
on-screen window is EARLIER than our timestamps; we shift back by those
measured lags and pad both ends. All four knobs are here, to tune against real
videos (audio landing late -> raise the lags/pre-roll).
"""

import json
import os
import time

from .translate import TranslationError, clean_word, translate

# Detection latency between a real on-screen event and our timestamp for it.
APPEAR_LAG = 0.30   # settle debounce (~0.1s) + up to one SCAN_INTERVAL (0.2s)
CLEAR_LAG = 0.35    # tracking.CLEAR_CONFIRM before a vanish is surfaced
# Safety padding so a word at the very edge of the line still has its audio.
PREROLL = 0.40
POSTROLL = 0.40
MAX_CLIP = 12.0      # cap when the line is still on screen (no cleared_at yet)
FALLBACK_CLIP = 6.0  # window ending "now" when there's no appear timestamp

CARDS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cards")


class CardDraft:
    """The gathered ingredients of one card draft.

    Paths are None when that piece couldn't be produced (no audio device, area
    untracked, translation offline); notes collects the human-readable reasons.
    """

    __slots__ = (
        "word", "word_translation", "sentence", "sentence_translation",
        "folder_path", "metadata_path", "image_path", "audio_path",
        "audio_seconds", "audio_window", "screenshot_source", "word_box",
        "sentence_box", "word_index", "sentence_verified", "appeared_at",
        "cleared_at", "created_at", "notes",
    )

    def __init__(self, word, sentence):
        self.word = word
        self.word_translation = ""
        self.sentence = sentence
        self.sentence_translation = ""
        self.folder_path = None
        self.metadata_path = None
        self.image_path = None
        self.audio_path = None
        self.audio_seconds = 0.0
        self.audio_window = None
        self.screenshot_source = None
        self.word_box = None
        self.sentence_box = None
        self.word_index = -1
        self.sentence_verified = False
        self.appeared_at = 0.0
        self.cleared_at = 0.0
        self.created_at = time.time()
        self.notes = []

    def summary(self):
        parts = [
            "word=%r -> %r" % (self.word, self.word_translation or "-"),
            "sentence=%r -> %r" % (
                self.sentence, self.sentence_translation or "-"),
            "folder=%s" % (
                os.path.basename(self.folder_path)
                if self.folder_path else "none"),
            "image=%s" % (
                os.path.basename(self.image_path) if self.image_path
                else "none"),
            "audio=%s (%.2fs)" % (
                os.path.basename(self.audio_path) if self.audio_path
                else "none", self.audio_seconds),
        ]
        if self.notes:
            parts.append("notes: " + "; ".join(self.notes))
        return " | ".join(parts)


def _audio_window(sentence, now):
    """Return the monotonic window to cut from the loopback buffer."""
    appeared = getattr(sentence, "appeared_at", 0.0) or 0.0
    cleared = getattr(sentence, "cleared_at", 0.0) or 0.0
    if appeared <= 0.0:
        return now - FALLBACK_CLIP, now
    t0 = appeared - APPEAR_LAG - PREROLL
    if cleared > 0.0:
        t1 = cleared - CLEAR_LAG + POSTROLL
    else:
        # Still on screen: end at now, but never run away.
        t1 = now
    if t1 - t0 > MAX_CLIP:
        t1 = t0 + MAX_CLIP
    if t1 <= t0:
        t1 = t0 + 0.5
    return t0, t1


def _next_card_dir(out_dir):
    """Create and return the next cards/card_0001-style draft folder."""
    os.makedirs(out_dir, exist_ok=True)
    i = 1
    while True:
        path = os.path.join(out_dir, "card_%04d" % i)
        try:
            os.mkdir(path)
            return path
        except FileExistsError:
            i += 1


def _relpath(path, root):
    if not path:
        return None
    return os.path.relpath(path, root).replace(os.sep, "/")


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


def _sentence_provenance(word, draft):
    """Validate that the clicked Word still belongs to the Sentence we save.

    The strongest proof is object identity: the overlay creates hotspots from
    sentence.words, so a clicked word should be one of those exact objects.
    The box containment check catches accidental stale/mismatched geometry.
    Text inclusion is recorded as a warning, not a hard failure, because OCR
    can split Japanese/CJK units differently from the displayed sentence.
    """
    sentence = getattr(word, "sentence", None)
    draft.word_box = _box_tuple(getattr(word, "box", None))
    draft.sentence_box = _box_tuple(getattr(sentence, "box", None))
    words = list(getattr(sentence, "words", []) or [])

    identity_index = -1
    for i, candidate in enumerate(words):
        if candidate is word:
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


def capture_png(region):
    """PNG bytes for the tracked area (physical (l, t, w, h)).

    A fresh mss instance is used because this runs off the capture thread. The
    overlay and popup are excluded from capture, so they don't appear in the
    shot.
    """
    import mss
    import mss.tools
    left, top, width, height = region
    with mss.mss() as sct:
        shot = sct.grab({"left": int(left), "top": int(top),
                         "width": int(width), "height": int(height)})
    return mss.tools.to_png(shot.rgb, shot.size)


def _grab_png(region, path):
    """Screenshot the tracked area (physical (l, t, w, h)) to a PNG file."""
    data = capture_png(region)
    with open(path, "wb") as f:
        f.write(data)


def _write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text or "")


def _write_artifacts(draft):
    folder = draft.folder_path
    _write_text(os.path.join(folder, "word.txt"), draft.word)
    _write_text(os.path.join(folder, "sentence.txt"), draft.sentence)
    _write_text(os.path.join(folder, "word_translation.txt"),
                draft.word_translation)
    _write_text(os.path.join(folder, "sentence_translation.txt"),
                draft.sentence_translation)
    if draft.notes:
        _write_text(os.path.join(folder, "notes.txt"), "\n".join(draft.notes))

    metadata = {
        "created_at": draft.created_at,
        "word": draft.word,
        "sentence": draft.sentence,
        "word_translation": draft.word_translation,
        "sentence_translation": draft.sentence_translation,
        "sentence_verified": draft.sentence_verified,
        "word_index": draft.word_index,
        "word_box": list(draft.word_box) if draft.word_box else None,
        "sentence_box": (
            list(draft.sentence_box) if draft.sentence_box else None
        ),
        "appeared_at_monotonic": draft.appeared_at,
        "cleared_at_monotonic": draft.cleared_at,
        "screenshot": _relpath(draft.image_path, folder),
        "screenshot_source": draft.screenshot_source,
        "audio": _relpath(draft.audio_path, folder),
        "audio_seconds": draft.audio_seconds,
        "audio_window": draft.audio_window,
        "notes": list(draft.notes),
    }
    draft.metadata_path = os.path.join(folder, "metadata.json")
    with open(draft.metadata_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
        f.write("\n")


def build_draft(word, region, recorder, out_dir=CARDS_DIR, translator=translate,
                screenshotter=_grab_png, screenshot_png=None,
                screenshot_note=""):
    """Gather one card's ingredients.

    region is the tracked area as physical (l, t, w, h) or None; recorder is a
    LoopbackRecorder or None. Blocking - run off the UI thread. Never raises
    for a missing piece: it records the reason in draft.notes and returns a
    partial draft folder.
    """
    now = time.monotonic()
    sentence = getattr(word, "sentence", None)
    raw_word = getattr(word, "text", "")
    draft = CardDraft(clean_word(raw_word) or raw_word,
                      getattr(sentence, "text", "") or "")
    draft.appeared_at = getattr(sentence, "appeared_at", 0.0)
    draft.cleared_at = getattr(sentence, "cleared_at", 0.0)
    draft.folder_path = _next_card_dir(out_dir)
    _sentence_provenance(word, draft)

    # --- translations (word + full sentence) -----------------------------
    try:
        draft.word_translation = translator(draft.word)
    except TranslationError as exc:
        draft.notes.append("word translation: %s" % exc)
    if draft.sentence:
        try:
            draft.sentence_translation = translator(draft.sentence)
        except TranslationError as exc:
            draft.notes.append("sentence translation: %s" % exc)

    # --- screenshot of the tracked area ----------------------------------
    if screenshot_png is not None:
        img_path = os.path.join(draft.folder_path, "screenshot.png")
        try:
            with open(img_path, "wb") as f:
                f.write(screenshot_png)
            draft.image_path = img_path
            draft.screenshot_source = "word_click"
        except Exception as exc:
            draft.notes.append("screenshot failed: %s" % exc)
    elif screenshot_note:
        draft.notes.append(screenshot_note)
    elif region is not None:
        img_path = os.path.join(draft.folder_path, "screenshot.png")
        try:
            screenshotter(region, img_path)
            draft.image_path = img_path
            draft.screenshot_source = "create_card"
        except Exception as exc:
            draft.notes.append("screenshot failed: %s" % exc)
    else:
        draft.notes.append("no tracked area for a screenshot")

    # --- audio clip from the rolling buffer ------------------------------
    if recorder is not None and getattr(recorder, "ready", False):
        t0, t1 = _audio_window(sentence, now)
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
    elif recorder is not None and getattr(recorder, "error", ""):
        draft.notes.append("audio: %s" % recorder.error)
    else:
        draft.notes.append("audio recorder not running")

    _write_artifacts(draft)
    return draft
