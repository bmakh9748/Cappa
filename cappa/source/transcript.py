"""A fetched caption track plus the OCR-line -> caption-window aligner.

The on-screen OCR gives us the *text* of a line; this finds that same line in
the caption track and hands back its real [start, end] timestamps, so a card's
audio can be cut from the downloaded audio at the exact moment -- no pixel
appear/clear guessing. Matching is fuzzy on purpose: OCR mis-reads a few
characters, manual subs are punctuated differently, auto subs split lines
oddly. We slide a word window over the token stream and keep the best
whole-sentence word overlap (blended with a character ratio), which tolerates
all three while resisting a coincidental shared tail.

When the caller passes `near_t` (the browser's playback position), the search is
restricted to captions within a time neighborhood of it -- so a line that reads
similarly minutes away can never win. This is the fix for auto-captions that are
simply wrong at the real spot: we look only where you actually are.

Pure and Qt-free."""

import html
import re
from difflib import SequenceMatcher

_EDGE = re.compile(r"^\W+|\W+$", re.UNICODE)  # leading/trailing punctuation
MIN_SCORE = 0.65          # below this we report no confident match
SEARCH_RADIUS = 20.0      # seconds around near_t to consider (when given)
WORD_MAX = 1.0            # cap on one token's spoken duration: the rolling
                          # VTT parser sets a token's end to the NEXT token's
                          # start, so the last word before a silence carries
                          # the whole silence in its raw end (card_0061:
                          # 'lagi' spanned 8.2s).
_SHRINK = 1               # candidate window may be at most 1 word shorter...
_GROW = 2                 # ...and up to 2 longer than the OCR line. Asymmetric
                          # on purpose: a shorter window can perfectly match a
                          # mere suffix of the line and score falsely high.
_WORD_WEIGHT = 0.6        # word overlap vs character ratio in the blend


class Transcript:
    """Timed word tokens for one video, plus its yt-dlp metadata."""

    def __init__(self, tokens, meta=None):
        self.tokens = list(tokens)
        self.meta = dict(meta or {})
        self._norm = [_norm(t.text) for t in self.tokens]

    def __bool__(self):
        return bool(self.tokens)

    def window_for(self, ocr_text, near_t=None, min_score=MIN_SCORE,
                   radius=SEARCH_RADIUS):
        """Best [start, end] window for an OCR line, or None if nothing clears
        `min_score`. When `near_t` (playback seconds) is given, only captions
        within `radius` seconds of it are considered. Returns a dict: start,
        end, score, text (the matched caption words), i, j (token index range),
        by='text'."""
        target = _tokens_of(ocr_text)
        if not target or not self.tokens:
            return None
        target_str = " ".join(target)
        norm = self._norm
        toks = self.tokens
        n = len(target)
        lo = None if near_t is None else near_t - radius
        hi = None if near_t is None else near_t + radius
        best = None  # (score, i, j)
        sizes = {max(1, n + d) for d in range(-_SHRINK, _GROW + 1)}
        for size in sizes:
            for i in range(0, len(norm) - size + 1):
                if lo is not None and (toks[i + size - 1].end < lo
                                       or toks[i].start > hi):
                    continue  # window falls outside the position neighborhood
                cand = norm[i:i + size]
                score = _similarity(target, target_str, cand)
                if best is None or score > best[0]:
                    best = (score, i, i + size)
        if best is None or best[0] < min_score:
            return None
        score, i, j = best
        # Last token's end capped like window_at's (card_0075) — see
        # _capped_end and the WORD_MAX note.
        return {
            "start": self.tokens[i].start,
            "end": _capped_end(self.tokens[j - 1]),
            "score": score,
            "text": " ".join(self.tokens[k].text for k in range(i, j)),
            "i": i,
            "j": j,
            "by": "text",
        }

    def window_at(self, t, gap=0.6, max_span=8.0):
        """Window of the caption line playing at time `t` (seconds into the
        video), independent of any text. This is the language-neutral path:
        when the burned-in subtitle is a translation that won't text-match the
        spoken-language track, the browser's playback position still pins the
        right moment. Groups the flat token stream into a line by expanding out
        from `t` while inter-word gaps stay under `gap` and the span under
        `max_span`. Returns the same dict shape as `window_for`, or None."""
        toks = self.tokens
        if not toks or t is None:
            return None
        idx = next((i for i, tok in enumerate(toks)
                    if tok.start <= t <= _capped_end(tok)), None)
        if idx is None:
            # t sits in a silence (or past the track). A word that starts
            # after t has not been SPOKEN yet at t, so it cannot be the line
            # the user is reading: the line that just played wins whenever
            # there is one. Its distance is measured from its capped END,
            # not its start — card_0077 clicked 2.1s after the last played
            # word ended, but that word's START was 3.1s back, so plain
            # nearest-start distance handed the click to the NEXT line,
            # 1.9s in the future. A future line is taken only when no past
            # word is within max_span (long silence, and the bridge's
            # position can run a touch behind a line that just started).
            prev = next((i for i in range(len(toks) - 1, -1, -1)
                         if toks[i].start <= t), None)
            nxt = 0 if prev is None else prev + 1
            if prev is not None and t - _capped_end(toks[prev]) <= max_span:
                idx = prev
            elif nxt < len(toks) and toks[nxt].start - t <= max_span:
                idx = nxt
            else:
                return None
        i = idx
        while (i > 0 and toks[i].start - _capped_end(toks[i - 1]) <= gap
               and _capped_end(toks[idx]) - toks[i - 1].start <= max_span):
            i -= 1
        j = idx
        while (j + 1 < len(toks)
               and toks[j + 1].start - _capped_end(toks[j]) <= gap
               and _capped_end(toks[j + 1]) - toks[i].start <= max_span):
            j += 1
        return {
            "start": toks[i].start,
            "end": _capped_end(toks[j]),
            "score": 0.0,
            "text": " ".join(toks[k].text for k in range(i, j + 1)),
            "i": i,
            "j": j + 1,
            "by": "position",
        }

    def sentence_for(self, ocr_text, near_t=None):
        """The whole track SENTENCE containing the caption `ocr_text`, or
        None when the text doesn't confidently match the track (a burned-in
        translation sub, garbage) — the match IS the safety gate. Returns
        {start, end, score, words: [(text, start, capped_end), ...]} with
        boundary markers ('>>') dropped from the words."""
        m = self.window_for(ocr_text, near_t=near_t)
        if m is None:
            return None
        i0, j0, capped = sentence_slice(self.tokens, m["i"], m["j"])
        if capped:
            # No real boundary before SENTENCE_MAX (see sentence_slice):
            # completing would merge unrelated sentences into the card
            # (card_0032) — keep the clean on-screen line.
            return None
        words = [(t.text, t.start, _capped_end(t))
                 for t in self.tokens[i0:j0] if not _marker(t)]
        if not words:
            return None
        return {"start": words[0][1], "end": words[-1][2],
                "score": m["score"], "words": words,
                # where the MATCHED on-screen text sits inside the sentence,
                # so the caller can stand it in as a row of its own
                "match_start": m["start"], "match_end": m["end"]}


_TERMINAL = ".?!…"     # a token ending with one of these ends a sentence
SENTENCE_GAP = 1.5     # no punctuation? a silence this long splits sentences
SENTENCE_MAX = 15.0    # a "sentence" longer than this is a runaway, stop


def _marker(tok):
    """A token with no letters or digits (the auto track's '>>' speaker
    change — kept HTML-escaped as '&gt;&gt;' in the VTT — stray symbols):
    a sentence boundary, never a word of one."""
    return not any(ch.isalnum() for ch in html.unescape(tok.text or ""))


def _ends_sentence(tok):
    return _marker(tok) or (tok.text or "").rstrip("\"'»)")[-1:] in tuple(
        _TERMINAL)


def sentence_slice(tokens, i, j):
    """Token range [i, j) grown to the whole SENTENCE it sits in: back to
    just after the previous terminal punctuation / speaker marker / big
    silence, forward until one. This is how a matched on-screen fragment
    learns 'there is more sentence' — the track is punctuated (or at least
    silence-split) where the word-at-a-time hardsub is not.

    Returns (i0, j0, capped). `capped` is True when growth stopped ONLY
    because the span reached SENTENCE_MAX — i.e. no real boundary
    (punctuation or silence) was ever found. That means the track has no
    sentence structure here (continuous unpunctuated auto-captions), and the
    'sentence' is really the whole neighbourhood; callers use it to refuse a
    runaway rather than merge unrelated lines."""
    i0 = i
    capped = False
    while i0 > 0:
        prev = tokens[i0 - 1]
        if (_ends_sentence(prev)
                or tokens[i0].start - _capped_end(prev) > SENTENCE_GAP):
            break
        if _capped_end(tokens[j - 1]) - prev.start > SENTENCE_MAX:
            capped = True
            break
        i0 -= 1
    j0 = j
    while j0 < len(tokens):
        if _ends_sentence(tokens[j0 - 1]):
            break
        nxt = tokens[j0]
        if nxt.start - _capped_end(tokens[j0 - 1]) > SENTENCE_GAP:
            break
        if _capped_end(nxt) - tokens[i0].start > SENTENCE_MAX:
            capped = True
            break
        j0 += 1
    return i0, j0, capped


def _capped_end(tok):
    """A token's end, trusted only up to WORD_MAX past its start (see the
    WORD_MAX note: raw ends absorb the silence after the last word)."""
    return min(tok.end, tok.start + WORD_MAX)


def _tokens_of(text):
    """OCR/caption text -> comparable words: whitespace-split, edge punctuation
    stripped, casefolded. Both the OCR line and the caption tokens go through
    the same normalization so 'pemikiran.' and 'pemikiran' compare equal."""
    return [w for w in (_norm(x) for x in (text or "").split()) if w]


def _norm(text):
    return _EDGE.sub("", text or "").casefold()


def _similarity(target_words, target_str, cand_words):
    """Blend of whole-sentence word overlap and a character ratio. Word overlap
    (order-aware) is the discriminator: it demands most of the line's words be
    present, so a match that only shares a trailing word or two scores low even
    when the characters look alike. The character ratio softens the edges for
    OCR mis-spellings within otherwise-correct words."""
    word_r = SequenceMatcher(None, target_words, cand_words).ratio()
    char_r = SequenceMatcher(None, target_str, " ".join(cand_words)).ratio()
    return _WORD_WEIGHT * word_r + (1.0 - _WORD_WEIGHT) * char_r
