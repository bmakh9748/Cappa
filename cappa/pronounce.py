"""The voice behind the popup's 🔊 — Google's free TTS endpoint, played local.

say(text, lang) is the whole story: fetch the word's MP3 from the key-less
translate_tts endpoint (the same no-key, no-LLM family translate.py uses),
cache the bytes so a re-click replays offline and instantly, write a temp
file, and hand it to winapi.play_mp3_blocking. Everything here BLOCKS — the
network round-trip and then the clip's own duration — so the popup calls it
on a helper thread and re-arms its button through a queued signal.

`lang` is the video's language code from settings (the same codes Google
Translate speaks: "id", "ja", "ar", …). "auto" cannot be spoken — the
endpoint needs a named voice — and the popup disables its button then
rather than let this raise. The endpoint hard-caps the text at 200
characters (201 answers HTTP 400, measured, not documented); callers pass
words, not paragraphs, so the cap is enforced rather than chunked around.

Failures raise PronounceError with a short displayable reason, the
translate.TranslationError pattern. requests does the fetch when installed
(it rides in with deep-translator, and this machine's cert store rejects
some chains under bare urllib); urllib is the fallback. No Qt."""

import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request

from . import winapi
from .dictionary import TIMEOUT, USER_AGENT   # one clock, one UA string

API = ("https://translate.google.com/translate_tts"
       "?ie=UTF-8&client=tw-ob&tl=%s&q=%s")
MAX_CHARS = 200   # the endpoint's hard cap: 200 chars is audio, 201 is a 400
_CACHE_MAX = 32   # ~8 KB of MP3 per word; enough for a viewing session
_cache = {}       # (text, lang) -> mp3 bytes


class PronounceError(Exception):
    """A failure whose str() is fit for the popup ('no audio — …')."""


def _fetch(text, lang):
    """Blocking: the MP3 bytes for `text` spoken in `lang`. Raises
    PronounceError; never returns junk (a non-audio body is a failure)."""
    url = API % (urllib.parse.quote(lang, safe=""),
                 urllib.parse.quote(text, safe=""))
    headers = {"User-Agent": USER_AGENT}
    try:
        try:
            import requests
        except ImportError:
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                    kind = r.headers.get("Content-Type", "")
                    data = r.read()
            except urllib.error.HTTPError as exc:
                if 400 <= exc.code < 500:
                    # The endpoint answered and said no: a language it has
                    # no voice for, not a connectivity problem.
                    raise PronounceError("no audio for this language")
                raise
        else:
            r = requests.get(url, headers=headers, timeout=TIMEOUT)
            if 400 <= r.status_code < 500:
                raise PronounceError("no audio for this language")
            r.raise_for_status()
            kind, data = r.headers.get("Content-Type", ""), r.content
    except PronounceError:
        raise
    except Exception as exc:
        raise PronounceError("no audio — check your internet") from exc
    if "audio" not in kind or not data:
        # A 200 with an HTML body (unknown language code, endpoint change).
        raise PronounceError("no audio for this language")
    return data


def fetch(text, lang):
    """The word's MP3, cached. Raises PronounceError (empty text, text over
    the endpoint's cap, network/endpoint trouble)."""
    text = (text or "").strip()
    if not text:
        raise PronounceError("nothing to pronounce")
    if len(text) > MAX_CHARS:
        raise PronounceError("too long to pronounce")
    key = (text, lang)
    hit = _cache.get(key)
    if hit is not None:
        return hit
    data = _fetch(text, lang)
    if len(_cache) >= _CACHE_MAX:
        _cache.clear()
    _cache[key] = data
    return data


def say(text, lang, still_wanted=None):
    """Blocking: pronounce `text` out loud and return when the audio ends.
    `still_wanted` (an optional callable) is consulted between the fetch
    and the play: the fetch can take seconds, and audio for a word the
    popup has already moved past should stay unplayed (the bytes are cached
    either way, so the next click on that word is instant). The temp file
    exists only around the play — play_mp3_blocking closes the MCI device
    before returning, which is what unlocks the file for the remove."""
    data = fetch(text, lang)
    if still_wanted is not None and not still_wanted():
        return
    fd, path = tempfile.mkstemp(suffix=".mp3")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        winapi.play_mp3_blocking(path)
    except OSError as exc:
        raise PronounceError("audio playback failed: %s" % exc) from exc
    finally:
        try:
            os.remove(path)
        except OSError:
            pass   # a straggler temp file must not mask the real outcome
