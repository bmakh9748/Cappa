"""Word translation behind the popup — free Google Translate, NO Claude.

One blocking function: translate(word, sentence) -> the word's
TARGET_LANGUAGE meaning. Goes through deep-translator's GoogleTranslator
(the free web endpoint: no API key, no per-click cost — the user's explicit
call; do not swap an LLM back in here). The popup runs it on a helper
thread (network round-trip; the UI thread must never wait) and shows what
comes back — the translation, or a TranslationError's message (no
internet, nothing returned). Results are cached, so re-clicking a word is
instant and off the network.

`sentence` is the word's OCR line and drives CONTEXT: the word is marked
with double quotes inside the sentence, the whole thing is translated, and
the marked span of the result is the answer — Google translates the word
as the sentence uses it (Arabic معروف alone comes back as the noun
'favor'; inside وغير معروف it becomes 'known'). Quotes usually survive
translation; whenever they don't (dropped, moved, or Google leaves the
quoted word untranslated) the bare-word translation is the fallback, so
context can only improve on the old behaviour, never lose it. The quotes
can also DRIFT: when the marked word fuses with a neighbour into one
phrase, Google puts the quotes around the neighbour's translation
(card_0047: SEKALIAN came back "GET OUT" — DIKELUARIN's meaning). A span
whose back-translation names a different word of the sentence is rejected
the same way, falling back to the bare word.

Case: hardsub captions SHOUT, and Google half-guesses all-caps words it
would translate fine in lowercase (card_0052: KELAR -> 'GONE', kelar ->
'finished'). Fully-uppercase input is therefore translated in lowercase,
and the answer keeps Google's natural casing — a card full of shouted
translations is unreadable (user call: never mirror the caption's caps).

SOURCE_LANGUAGE defaults to auto-detect, but auto-detect fails on lone words
(a bare "BAWA" comes back "BAWA"): naming the video's language via the settings
panel makes single words translate ("BAWA" -> "BRING"). Both SOURCE_LANGUAGE and
TARGET_LANGUAGE are driven from settings. No Qt; the deep_translator import is
deferred so the app and test suite run without the package — only clicking a
word touches it."""

import re
import unicodedata

SOURCE_LANGUAGE = "auto"   # the video's language; set from the settings panel
TARGET_LANGUAGE = "en"     # the user's language
_CACHE_MAX = 256

_cache = {}   # (word, sentence) -> translation

# Quote characters Google may turn our ASCII "…" marks into on the way
# through a translation (locale quotes: curly, guillemets, low-9, CJK
# corners). ASCII apostrophes are NOT here: they appear inside words.
_QUOTES = ("\"“”«»„‟‹›"
           "〝〞「」『』＂")
_MARKED = re.compile("[%s]([^%s]+)[%s]" % ((re.escape(_QUOTES),) * 3))


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


def _mark(sentence, word):
    """`sentence` with its first standalone occurrence of `word` wrapped in
    double quotes, or None when the word isn't in the sentence. Standalone
    first ('he' must not mark the middle of 'the'); a plain substring search
    is the fallback for scripts without word boundaries (CJK)."""
    if not word or not sentence or not word.strip():
        return None
    # The sentence's OWN quote marks collide with ours: the span reader
    # would hand back the sentence's quoted word instead of the marked one
    # (card_0044: KARENA came back as the sentence's “VIRAL”). Blank them
    # out of the copy that goes to the translator.
    sentence = re.sub("[%s]" % re.escape(_QUOTES), " ", sentence)
    sentence = re.sub(r"\s{2,}", " ", sentence).strip()
    m = re.search(r"(?<!\w)%s(?!\w)" % re.escape(word), sentence,
                  re.IGNORECASE)
    if m is not None:
        lo, hi = m.span()
    else:
        lo = sentence.casefold().find(word.casefold())
        if lo < 0:
            return None
        hi = lo + len(word)
    return '%s"%s"%s' % (sentence[:lo], sentence[lo:hi], sentence[hi:])


def _extract_marked(text, word):
    """The quoted span the translation carried through, or '' when the marks
    didn't survive or the span doesn't look like one word's meaning (too
    long: the quotes drifted). A span identical to the source word means
    Google treated the quotes as 'keep this as-is' — also a miss; the
    bare-word fallback covers it."""
    m = _MARKED.search(text or "")
    if not m:
        return ""
    span = m.group(1).strip()
    if not span or len(span) > 60 or len(span.split()) > 5:
        return ""
    if span.casefold() == (word or "").casefold():
        return ""
    return span


def _span_points_elsewhere(span, word, sentence):
    """True when the marked span is really a DIFFERENT word's translation:
    its back-translation into SOURCE_LANGUAGE matches another word of the
    sentence and not the marked one (card_0047: "GET OUT" back-translates
    to KELUAR — the stem of the neighbouring DIKELUARIN, not of SEKALIAN).
    Substring matching absorbs affixes (KELUAR ⊂ DIKELUARIN, BAWA ⊂
    MEMBAWA); tokens under 3 chars are noise (di-, ke-). Needs a named
    SOURCE_LANGUAGE to translate back into, and is best-effort: 'auto' or
    a failed round-trip accepts the span rather than lose a good one."""
    if SOURCE_LANGUAGE == "auto":
        return False
    try:
        from deep_translator import GoogleTranslator
        back = GoogleTranslator(source=TARGET_LANGUAGE,
                                target=SOURCE_LANGUAGE).translate(span) or ""
    except Exception:
        return False
    back = back.strip().casefold()
    wordf = (word or "").casefold()
    if not back or back in wordf or wordf in back:
        return False
    for token in sentence.split():
        tok = clean_word(token).casefold()
        if not tok or wordf in tok:
            continue   # the marked word itself isn't "elsewhere"
        if len(tok) >= 3 and (back in tok or tok in back):
            return True
    return False


def _deshout(text):
    """ALL-CAPS input lowered for the translator, other text untouched.
    Hardsub fonts shout, and Google translates shouted Indonesian on a
    guess-by-shape basis: KELAR (finished) came back 'GONE' bare and
    'GET OUT' in context, while kelar comes back 'finished' (card_0052).
    Mixed case is meaningful (German nouns, names) and passes through."""
    return text.lower() if (text or "").isupper() else text


def translate(word, sentence=""):
    """Blocking: the TARGET_LANGUAGE meaning of `word` — as its `sentence`
    uses it when possible (see the module header), the bare word otherwise.
    Shouted input is translated in lowercase and the answer stays in
    Google's natural casing (never the caption's caps — user call).
    Raises TranslationError with a short, displayable reason."""
    key = (word, sentence or "")
    hit = _cache.get(key)
    if hit is not None:
        return hit
    try:
        from deep_translator import GoogleTranslator
    except ImportError:
        raise TranslationError("deep-translator not installed")
    translator = GoogleTranslator(source=SOURCE_LANGUAGE,
                                  target=TARGET_LANGUAGE)
    text = ""
    marked = _mark(_deshout(sentence), word)
    if marked:
        try:
            text = _extract_marked(translator.translate(marked), word)
        except Exception as exc:
            raise TranslationError(
                "no translation — check your internet") from exc
        if text and _span_points_elsewhere(text, word, sentence):
            text = ""   # quotes drifted onto a neighbour; use the bare word
    if not text:
        # No sentence, the word wasn't in it, or the marks didn't survive.
        try:
            text = (translator.translate(_deshout(word)) or "").strip()
        except Exception as exc:
            raise TranslationError(
                "no translation — check your internet") from exc
    if not text:
        raise TranslationError("no translation returned")
    if len(_cache) >= _CACHE_MAX:
        _cache.clear()
    _cache[key] = text
    return text
