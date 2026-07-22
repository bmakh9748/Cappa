"""Example sentences for a clicked word — the popup's Examples tab.

Three sources, tried in order of trust, topped up to LIMIT:

  1. Japanese: the JMdict pack itself (schema 3 carries per-entry Tatoeba
     jp/en pairs — see jmdict.py). Offline, instant, and keyed by the
     resolved dictionary form, so an inflected click still finds them.
  2. The word's own Wiktionary entry (dictionary.examples): hand-picked by
     editors and carrying an English translation for foreign words. Shares
     the definition fetch's page cache, so a popup that already showed the
     meaning pays no second request. English-target only, the same
     constraint dictionary.meaning lives by (the translations are English).
  3. Tatoeba's key-less API: exact-form search ("=word" — the Manticore
     operator), so every sentence contains the word AS CLICKED, with a
     translation in the user's target language guaranteed by the trans
     filter. ~2 s a call, which is why the popup only asks when the
     Examples tab is actually opened.

Sentences longer than MAX_CHARS are skipped — the popup is a popup, not a
reader. Results are Example objects: the sentence, its translation, an
optional transliteration (Arabic), the surface form worth bolding, and the
source name (Tatoeba sentences are CC-BY; the popup shows the credit).

Blocking network — callers keep this off the UI thread, like translate().
Free, key-less, NO LLM — the house rule. No Qt."""

import json
import urllib.parse
import urllib.request

from . import dictionary
from .japanese import jmdict
from . import translate as translate_mod
from .dictionary import TIMEOUT, USER_AGENT   # one clock, one UA string

LIMIT = 3       # examples the popup shows: enough to see the word in the
                # wild, few enough that the popup doesn't blanket the caption
                # (an open popup swallows every hotspot under its geometry)
MAX_CHARS = 90  # skip sentences that would wrap into a paragraph
FETCH = 10      # Tatoeba rows requested; the length filter eats some

API = ("https://api.tatoeba.org/unstable/sentences"
       "?lang=%s&q=%s&trans%%3Alang=%s&sort=relevance&limit=%d")

# settings codes (Google's) -> Tatoeba's ISO 639-3, for the reduced roster
# (en is the target side). A language missing here just skips the Tatoeba
# top-up; the other sources still answer.
_ISO3 = {"en": "eng", "ar": "ara", "id": "ind", "ja": "jpn"}

_CACHE_MAX = 128  # a viewing session's worth of clicked words; entries are
                  # a few short strings each, so memory stays trivial
_cache = {}   # (word, lemma, source_lang, target_lang) -> [Example]


class ExamplesError(Exception):
    """A failure whose str() is fit for the popup ('no examples — …')."""


class Example:
    """One example sentence, ready to render."""

    __slots__ = ("text", "translation", "translit", "form", "source")

    def __init__(self, text, translation="", translit="", form="",
                 source=""):
        self.text = text                # the sentence, in the video's language
        self.translation = translation  # its rendering in the user's language
        self.translit = translit        # romanization, when the source has one
        self.form = form                # the surface worth bolding in text
        self.source = source            # who to credit ("Tatoeba", …)

    def __repr__(self):
        return "Example(%r -> %r)" % (self.text, self.translation)


def _get_json(url):
    """One JSON GET, requests first (same cert-store story as
    dictionary._fetch). Raises on any trouble."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    try:
        import requests
    except ImportError:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    r = requests.get(url, headers=headers, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _from_pack(form):
    """The JMdict entry's own examples (offline). [] without a pack or when
    the entry carries none. The same MAX_CHARS cap as the web sources — a
    pack sentence can be a paragraph too."""
    entries = jmdict.lookup(form)
    if not entries:
        return []
    return [Example(jp, en, form=used or form, source="Tatoeba")
            for jp, en, used in entries[0].examples
            if len(jp) <= MAX_CHARS]


def _from_tatoeba(word, src, tgt):
    """Exact-form Tatoeba search, filtered to popup-sized sentences with a
    translation in the target language. [] when the language pair isn't
    mapped (not a failure — there is just nothing to ask); None on network
    trouble."""
    src3, tgt3 = _ISO3.get(src), _ISO3.get(tgt)
    if not src3 or not tgt3:
        return []
    url = API % (src3, urllib.parse.quote("=" + word, safe=""), tgt3, FETCH)
    try:
        data = _get_json(url)
    except Exception:
        return None
    out = []
    for row in data.get("data") or []:
        text = (row.get("text") or "").strip()
        if not text or len(text) > MAX_CHARS or row.get("is_unapproved"):
            continue   # unapproved sentences are unvetted; learners deserve
                       # the reviewed corpus only
        # The trans filter guarantees a target-language translation exists;
        # prefer a directly linked one over a pivot through a third language.
        best = ""
        for t in row.get("translations") or []:
            if t.get("lang") != tgt3 or t.get("is_unapproved"):
                continue
            if not best or t.get("is_direct"):
                best = (t.get("text") or "").strip() or best
                if best and t.get("is_direct"):
                    break
        if best:
            out.append(Example(text, best, form=word, source="Tatoeba"))
    return out


def _dedupe(items):
    seen, out = set(), []
    for ex in items:
        key = ex.text.casefold()
        if key not in seen:
            seen.add(key)
            out.append(ex)
    return out


def sentences(word, lemma=None, limit=LIMIT):
    """Blocking: up to `limit` Examples for a clicked word, best source
    first. [] when every source legitimately came up empty; raises
    ExamplesError only when a source FAILED (network) and nothing else
    answered — the popup shows the reason instead of a silent blank."""
    src = translate_mod.SOURCE_LANGUAGE
    tgt = translate_mod.TARGET_LANGUAGE
    key = (word, lemma or "", src, tgt)
    hit = _cache.get(key)
    if hit is not None:
        return hit[:limit]

    out, failed = [], False
    if src == jmdict.LANG:
        # The pack is the Japanese source of truth (dictionary.py's rule);
        # Wiktionary's Japanese sections are skipped for the same reason.
        out.extend(_from_pack(lemma or word))
    elif src != "auto" and tgt == "en":
        wikt = dictionary.examples(word, src)
        if wikt is None:
            failed = True
        else:
            out.extend(Example(text, trans, translit, form=word,
                               source="Wiktionary")
                       for text, trans, translit in wikt
                       if len(text) <= MAX_CHARS)
    if len(out) < limit:
        more = _from_tatoeba(word, src, tgt)
        if more is None:
            failed = True
        else:
            out.extend(more)

    out = _dedupe(out)[:limit]
    if not out and failed:
        raise ExamplesError("no examples — check your internet")
    if not failed or len(out) >= limit:
        # Cache only complete or fully-answered results, so the next click
        # retries the failed source (dictionary.py's page-cache rule).
        if len(_cache) >= _CACHE_MAX:
            _cache.clear()
        _cache[key] = out
    return out
