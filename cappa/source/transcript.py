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

import re
from difflib import SequenceMatcher

_EDGE = re.compile(r"^\W+|\W+$", re.UNICODE)  # leading/trailing punctuation
MIN_SCORE = 0.65          # below this we report no confident match
SEARCH_RADIUS = 20.0      # seconds around near_t to consider (when given)
WORD_MAX = 1.0            # effective cap on one token's spoken duration. The
                          # rolling-VTT parser sets a token's end to the NEXT
                          # token's start, so the last word before a silence
                          # carries the whole silence in its raw end
                          # (card_0061: 'lagi' spanned 8.2s) — and that one
                          # phantom-long word poisoned every gap and span
                          # test around it, cutting the real sentence out of
                          # the position window.
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

    def full_text(self):
        return " ".join(t.text for t in self.tokens)

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
        # The last token's end is capped like window_at's (card_0075): in the
        # rolling format a word's raw end is the NEXT word's start, so an
        # uncapped end absorbs the inter-sentence silence and the clip runs
        # into the next line's audio.
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
            # t sits in a silence (or past the track): the nearest word is
            # still the line playing/last played there.
            idx = min(range(len(toks)), key=lambda i: abs(toks[i].start - t))
            if abs(toks[idx].start - t) > max_span:
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
