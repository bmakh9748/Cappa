"""Word meanings from Wiktionary -- definitions, not translations.

Google's in-context trick answers "what does this word do in THIS
sentence's English rendering", which reads like a wrong translation
whenever the word is grammatical glue or fuses into a phrase (card_0065:
Indonesian YANG came back "sees"; card_0066: Ngupil came back "picking her
nose" -- the whole predicate). A dictionary answers what the WORD means.
English Wiktionary's REST API serves per-language definitions with part of
speech, free and key-less -- the same no-LLM, no-key rule translate.py
lives by.

meaning() is what the popup and the card builder call. When definitions
exist, they are shown -- part-of-speech lines, ordered so the sense that
agrees with Google's in-context translation comes first (LIAT in a
see-sentence lists "alternative form of lihat (to see...)" above its
"rubbery" adjective sense). When they don't (niche colloquialisms like
ngupil have no entry; a misread OCR word never will), the answer is
exactly the old contextual translation, so nothing gets worse. Definitions
need the video's language NAMED in settings (the response is keyed by
language section) and an English target (en.wiktionary defines in
English); otherwise meaning() is a pass-through to translate().

Blocking network calls -- callers keep this off the UI thread, like
translate(). requests does the fetch when installed (it rides in with
deep-translator, and some machines' cert stores — this one's included —
reject Wikimedia's chain under bare urllib, which silently degraded every
definition to the Google fallback); urllib remains the no-requests path.
No Qt."""

import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from . import jmdict
from . import translate as translate_mod
from .translate import TranslationError

API = "https://en.wiktionary.org/api/rest_v1/page/definition/%s"
# Wikimedia asks API clients to identify themselves; anonymous UAs get 403s.
USER_AGENT = "Cappa/0.1 (local language-learning flashcard app)"
TIMEOUT = 6.0

MAX_POS_LINES = 3     # at most this many "pos: ..." lines on the card
MAX_SENSES = 2        # senses kept per part of speech
MAX_SENSE_CHARS = 90  # a long encyclopedic gloss is trimmed, not shown whole

# settings.py language codes -> en.wiktionary section keys. Empty since the
# roster reduced to ar/ja/id (all 1:1); the mapping stays for the day a
# non-1:1 code (zh-CN -> zh) returns.
_SECTION_FOR = {}

_CACHE_MAX = 256
_cache = {}   # (word, sentence, source_lang) -> formatted text

_TAG = re.compile(r"<[^>]+>")
_WORDISH = re.compile(r"[^\W\d_]{3,}")


def _clean_gloss(markup):
    """A definition's HTML reduced to plain text: tags out, entities
    decoded, whitespace collapsed."""
    text = html.unescape(_TAG.sub("", markup or ""))
    return re.sub(r"\s+", " ", text).strip()


def _fetch(word):
    """The definition page for `word`, parsed; None when there is no such
    page (404). requests first — see the module header for why bare urllib
    is not enough on every machine."""
    url = API % urllib.parse.quote(word, safe="")
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    try:
        import requests
    except ImportError:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise
    r = requests.get(url, headers=headers, timeout=TIMEOUT)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def lookup(word, lang):
    """Wiktionary entries for `word` in the video's language: a list of
    (part_of_speech, [sense, ...]) in page order. [] when the word has no
    entry in that language; None on network trouble (the caller falls back
    the same way for both). Hardsubs SHOUT and page titles are
    case-sensitive, so the lowercased word is tried first, the original
    spelling second."""
    section = _SECTION_FOR.get(lang, lang)
    for candidate in dict.fromkeys((word.lower(), word)):
        try:
            data = _fetch(candidate)
        except Exception:
            return None
        if data is None:
            continue              # no such page; maybe the other casing
        entries = []
        for entry in data.get(section) or []:
            senses = []
            for d in entry.get("definitions") or []:
                text = _clean_gloss(d.get("definition"))
                if text and text not in senses:
                    senses.append(text)
            if senses:
                entries.append(((entry.get("partOfSpeech") or "").lower(),
                                senses))
        if entries:
            return entries
    return []


# A bare "passive of pikir" teaches nothing without pikir's meaning
# (card_0073). Wiktionary form-of glosses sometimes carry the base's
# meaning in parentheses and sometimes don't; when one ends by naming its
# base word bare, the base's own first sense is fetched and carried along.
_FORM_OF = re.compile(r"\bof ([^\W\d_-]+)$")
_BASE_GLOSS_CHARS = 60


def _enrich_form_of(entries, lang):
    out = []
    for pos, senses in entries:
        enriched = []
        for sense in senses:
            m = _FORM_OF.search(sense)
            if m and "(" not in sense:
                base = lookup(m.group(1), lang)
                if base and base[0][1]:
                    gloss = base[0][1][0]
                    if len(gloss) > _BASE_GLOSS_CHARS:
                        gloss = gloss[:_BASE_GLOSS_CHARS - 1].rstrip() + "…"
                    sense = "%s (%s)" % (sense, gloss)
            enriched.append(sense)
        out.append((pos, enriched))
    return out


def _japanese(word):
    """A JMdict entry as card text, or "" when the pack has nothing (or
    isn't downloaded yet — then the caller falls through to translate())."""
    entries = jmdict.lookup(word)
    if not entries:
        return ""
    entry = entries[0]
    head = entry.headword
    if entry.reading and entry.reading != head:
        head = "%s 【%s】" % (head, entry.reading)
    lines = [head]
    tags = entry.tags()
    if tags:
        lines.append(" · ".join(tags))
    for i, (_pos, glosses) in enumerate(entry.senses[:MAX_SENSES], 1):
        lines.append("%d. %s" % (i, "; ".join(glosses[:3])))
    return "\n".join(lines)


def _hint_tokens(hint):
    return set(_WORDISH.findall((hint or "").casefold()))


def _matches_hint(senses, tokens):
    """Whether any sense shares a word with the in-context translation.
    Prefix matching either way absorbs inflection ('sees' vs 'see')."""
    for sense in senses:
        for w in _WORDISH.findall(sense.casefold()):
            if any(w.startswith(t) or t.startswith(w) for t in tokens):
                return True
    return False


def _format(entries, hint=""):
    """Definition lines for the popup/card, one per part of speech, the
    entry agreeing with the sentence's own translation hoisted first (the
    sort is stable: everything else keeps Wiktionary's order)."""
    tokens = _hint_tokens(hint)
    ordered = sorted(entries,
                     key=lambda e: not _matches_hint(e[1], tokens))
    lines = []
    for pos, senses in ordered[:MAX_POS_LINES]:
        kept = [s if len(s) <= MAX_SENSE_CHARS else
                s[:MAX_SENSE_CHARS - 1].rstrip() + "…"
                for s in senses[:MAX_SENSES]]
        text = "; ".join(kept)
        lines.append("%s: %s" % (pos, text) if pos else text)
    return "\n".join(lines)


def meaning(word, sentence="", translate_fn=None):
    """The text the popup and the card show for a clicked word: dictionary
    definitions when Wiktionary has the word (context translation used to
    order the senses), the plain contextual translation otherwise. Raises
    TranslationError only when BOTH sources come up empty."""
    if translate_fn is None:
        translate_fn = translate_mod.translate
    lang = translate_mod.SOURCE_LANGUAGE
    if lang == jmdict.LANG:
        # Japanese has its own dictionary, offline and already downloaded:
        # Wiktionary has no useful per-word Japanese section and Google would
        # be handed a word the caller already resolved to its dictionary form.
        text = _japanese(word)
        if text:
            return text
    if lang == "auto" or translate_mod.TARGET_LANGUAGE != "en":
        # No section to read / definitions would be in the wrong language.
        return translate_fn(word, sentence)

    key = (word, sentence or "", lang)
    hit = _cache.get(key)
    if hit is not None:
        return hit

    hint, hint_error = "", None
    try:
        hint = translate_fn(word, sentence)
    except TranslationError as exc:
        hint_error = exc          # offline? the dictionary will fail too,
                                  # and this is the error worth showing then

    entries = lookup(word, lang)
    if entries:
        text = _format(_enrich_form_of(entries, lang), hint)
    else:
        text = hint
    if not text:
        raise hint_error or TranslationError("no translation returned")
    if len(_cache) >= _CACHE_MAX:
        _cache.clear()
    _cache[key] = text
    return text
