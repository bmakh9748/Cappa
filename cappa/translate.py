"""Word translation behind the popup — free Google Translate, NO Claude.

One blocking function: translate(word, sentence) -> the word's
TARGET_LANGUAGE meaning. Goes through deep-translator's GoogleTranslator
(the free web endpoint: no API key, no per-click cost — the user's explicit
call; do not swap an LLM back in here). The popup runs it on a helper
thread (network round-trip; the UI thread must never wait) and shows what
comes back — the translation, or a TranslationError's message (no
internet, nothing returned). Results are cached, so re-clicking a word is
instant and off the network.

`sentence` is accepted but unused today: Google translates the bare word.
It stays in the signature because the popup already has it and a
context-aware backend (or the Anki card) will want it.

SOURCE_LANGUAGE defaults to auto-detect, but auto-detect fails on lone words
(a bare "BAWA" comes back "BAWA"): naming the video's language via the settings
panel makes single words translate ("BAWA" -> "BRING"). Both SOURCE_LANGUAGE and
TARGET_LANGUAGE are driven from settings. No Qt; the deep_translator import is
deferred so the app and test suite run without the package — only clicking a
word touches it."""

import unicodedata

SOURCE_LANGUAGE = "auto"   # the video's language; set from the settings panel
TARGET_LANGUAGE = "en"     # the user's language
_CACHE_MAX = 256

_cache = {}   # word -> translation


class TranslationError(Exception):
    """A failure whose str() is fit for the popup ('no connection — …')."""


def set_target_language(code):
    """Set the language words are translated into (from the settings panel).
    Clears the cache, whose entries were for the previous target."""
    global TARGET_LANGUAGE
    if code and code != TARGET_LANGUAGE:
        TARGET_LANGUAGE = code
        _cache.clear()


def set_source_language(code):
    """Set the video's language (from the settings panel). 'auto' restores
    per-word auto-detect. Clears the cache, keyed by word for the old source."""
    global SOURCE_LANGUAGE
    if code and code != SOURCE_LANGUAGE:
        SOURCE_LANGUAGE = code
        _cache.clear()


def clean_word(text):
    """The word as the popup (and later the card) shows it: punctuation and
    symbols stripped from the EDGES only — OCR words carry the line's
    commas, quotes, brackets and ♪ marks, but inner marks are part of the
    word (don't, ハロー・ワールド). Unicode categories, not an ASCII list,
    so 「こんにちは」 and 'hello,' both come back bare."""
    chars = list((text or "").strip())
    while chars and unicodedata.category(chars[0])[0] in "PS":
        chars.pop(0)
    while chars and unicodedata.category(chars[-1])[0] in "PS":
        chars.pop()
    return "".join(chars).strip()


def translate(word, sentence=""):
    """Blocking: the TARGET_LANGUAGE meaning of `word`. Raises
    TranslationError with a short, displayable reason."""
    hit = _cache.get(word)
    if hit is not None:
        return hit
    try:
        from deep_translator import GoogleTranslator
    except ImportError:
        raise TranslationError("deep-translator not installed")
    try:
        text = GoogleTranslator(source=SOURCE_LANGUAGE,
                                target=TARGET_LANGUAGE).translate(word)
    except Exception as exc:
        raise TranslationError("no translation — check your internet") from exc
    text = (text or "").strip()
    if not text:
        raise TranslationError("no translation returned")
    if len(_cache) >= _CACHE_MAX:
        _cache.clear()
    _cache[word] = text
    return text
