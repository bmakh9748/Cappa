"""Assemble a flashcard from a clicked word — everything but the .apkg.

On click we already have the Word, and through it the Sentence it sits in
plus that sentence's on-screen appear/clear timestamps. build_draft() turns
that into a CardDraft: the word and its translation, the full sentence and
its translation, a PNG screenshot of the tracked area, and — the hard part —
a WAV of the audio that played while the sentence was on screen, cut from the
recorder's rolling buffer.

The genanki .apkg export is the next step and reads these CardDraft fields;
this module just gathers and saves the ingredients. build_draft() blocks
(two network translations + a screen grab + a WAV write), so callers run it
off the UI thread — the popup does, on its own helper thread.

Timing — WHY the clip isn't just [appeared_at, cleared_at]: our detection
lags the real screen. A line is boxed up to a scan-interval + settle after it
actually appears, and a clear is only confirmed after a debounce. So the true
on-screen window is EARLIER than our timestamps; we shift back by those
measured lags and pad both ends. All four knobs are here, to tune against
real videos (audio landing late -> raise the lags/pre-roll)."""

import os
import time

from .translate import TranslationError, clean_word, translate

# Detection latency between a real on-screen event and our timestamp for it.
APPEAR_LAG = 0.30   # settle debounce (~0.1s) + up to one SCAN_INTERVAL (0.2s)
CLEAR_LAG = 0.35    # tracking.CLEAR_CONFIRM before a vanish is surfaced
# Safety padding so a word at the very edge of the line still has its audio.
PREROLL = 0.40
POSTROLL = 0.40
MAX_CLIP = 12.0     # cap when the line is still on screen (no cleared_at yet)
FALLBACK_CLIP = 6.0  # window ending "now" when there's no appear timestamp

CARDS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cards")


class CardDraft:
    """The gathered ingredients of one card. Paths are None when that piece
    couldn't be produced (no audio device, area untracked, translation
    offline); `notes` collects the human-readable reasons."""

    __slots__ = ("word", "word_translation", "sentence", "sentence_translation",
                 "image_path", "audio_path", "audio_seconds",
                 "appeared_at", "cleared_at", "created_at", "notes")

    def __init__(self, word, sentence):
        self.word = word
        self.word_translation = ""
        self.sentence = sentence
        self.sentence_translation = ""
        self.image_path = None
        self.audio_path = None
        self.audio_seconds = 0.0
        self.appeared_at = 0.0
        self.cleared_at = 0.0
        self.created_at = time.time()
        self.notes = []

    def summary(self):
        parts = ["word=%r -> %r" % (self.word, self.word_translation or "—"),
                 "sentence=%r -> %r" % (self.sentence,
                                        self.sentence_translation or "—"),
                 "image=%s" % (os.path.basename(self.image_path)
                               if self.image_path else "none"),
                 "audio=%s (%.2fs)" % (os.path.basename(self.audio_path)
                                       if self.audio_path else "none",
                                       self.audio_seconds)]
        if self.notes:
            parts.append("notes: " + "; ".join(self.notes))
        return " | ".join(parts)


def _audio_window(sentence, now):
    """(t0, t1) monotonic window to cut audio from, shifted earlier by the
    detection lags and padded. Falls back to a fixed window ending now when
    the sentence carries no appear timestamp."""
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


def _grab_png(region, path):
    """Screenshot the tracked area (physical (l, t, w, h)) to a PNG. A fresh
    mss instance because this runs off the capture thread; the overlay and
    popup are excluded from capture, so they don't appear in the shot."""
    import mss
    import mss.tools
    left, top, width, height = region
    with mss.mss() as sct:
        shot = sct.grab({"left": int(left), "top": int(top),
                         "width": int(width), "height": int(height)})
    mss.tools.to_png(shot.rgb, shot.size, output=path)


def build_draft(word, region, recorder, out_dir=CARDS_DIR, translator=translate):
    """Gather one card's ingredients. `region` is the tracked area as physical
    (l, t, w, h) or None; `recorder` is a LoopbackRecorder or None. Blocking —
    run off the UI thread. Never raises for a missing piece: it records the
    reason in draft.notes and returns a partial draft."""
    now = time.monotonic()
    sentence = word.sentence
    draft = CardDraft(clean_word(word.text) or word.text,
                      sentence.text if sentence else "")
    draft.appeared_at = getattr(sentence, "appeared_at", 0.0)
    draft.cleared_at = getattr(sentence, "cleared_at", 0.0)
    os.makedirs(out_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S") + "_%03d" % (int(now * 1000) % 1000)

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
    if region is not None:
        img_path = os.path.join(out_dir, "card_%s.png" % stamp)
        try:
            _grab_png(region, img_path)
            draft.image_path = img_path
        except Exception as exc:
            draft.notes.append("screenshot failed: %s" % exc)
    else:
        draft.notes.append("no tracked area for a screenshot")

    # --- audio clip from the rolling buffer ------------------------------
    if recorder is not None and getattr(recorder, "ready", False):
        t0, t1 = _audio_window(sentence, now)
        wav_path = os.path.join(out_dir, "card_%s.wav" % stamp)
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
    elif recorder is not None and recorder.error:
        draft.notes.append("audio: %s" % recorder.error)
    else:
        draft.notes.append("audio recorder not running")

    return draft
