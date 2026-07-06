"""Parse WebVTT caption files into a flat stream of timed word Tokens.

Two shapes of file come off YouTube and both land here:

* Uploader / manual subs: clean cue blocks -- one `HH:MM:SS.mmm --> ...`
  window holding a line of text. No per-word timing, so each word inherits a
  slice of its line's window (spread by character length).

* Auto-generated subs: the "rolling" format. Each cue repaints the previous
  line as settled plain text and grows the *current* line one word at a time,
  every word carrying its own inline `<00:00:01.920><c> word</c>` timestamp.
  The file is padded with thousands of 10ms flip-cues that just re-show text,
  and with `[Musik]`/`[Applause]` noise. We reconstruct clean, de-duplicated
  per-word timing from the inline tags (prefix-diffing consecutive lines so a
  line that grows across cues contributes each word once) -- which gives the
  tightest possible audio bound: the exact words an OCR line covers.

Pure and Qt-free. Output is `List[Token]` in time order; a word's `end` is the
next word's start, so a matched run's [first.start, last.end] is its window."""

import re
from collections import namedtuple

Token = namedtuple("Token", "text start end")

# HH:MM:SS.mmm  (comma decimals tolerated for stray SRT-ish files)
_TS = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[.,](\d{3})")
# a cue header line:  <start> --> <end>  [cue settings we ignore]
_CUE = re.compile(r"(\d{1,2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*"
                  r"(\d{1,2}:\d{2}:\d{2}[.,]\d{3})")
# an inline per-word timestamp tag in auto captions: <00:00:01.920>
_INLINE = re.compile(r"<(\d{1,2}:\d{2}:\d{2}[.,]\d{3})>")
_CTAG = re.compile(r"</?c[^>]*>")          # <c>, <c.colorXXXXXX>, </c>
_BRACKET = re.compile(r"\[[^\]]*\]")       # [Musik], [Applause], ...
_MIN_DUR = 0.05                            # floor so end never precedes start
_LAST_DUR = 0.6                            # assumed length of the final word


def parse_vtt(content):
    """Parse VTT text into `List[Token]`. Chooses the manual or auto path by
    whether inline per-word timestamps are present."""
    cues = _split_cues(content)
    if _INLINE.search(content):
        tokens = _tokens_auto(cues)
    else:
        tokens = _tokens_manual(cues)
    return _fill_ends(tokens)


# --------------------------------------------------------------- cue splitting
def _split_cues(content):
    """[(start, end, [raw_text_lines])] for every cue. A truly empty line ('')
    ends a cue; a whitespace-only line (' ') is kept -- auto captions use it as
    a content placeholder, so `.strip()`-based splitting would cut cues short."""
    lines = content.splitlines()
    cues = []
    i = 0
    while i < len(lines):
        m = _CUE.search(lines[i])
        if not m:
            i += 1
            continue
        start, end = _secs(m.group(1)), _secs(m.group(2))
        i += 1
        text = []
        while i < len(lines) and lines[i] != "" and not _CUE.search(lines[i]):
            text.append(lines[i])
            i += 1
        cues.append((start, end, text))
    return cues


# ------------------------------------------------------------------- manual
def _tokens_manual(cues):
    tokens = []
    for start, end, text_lines in cues:
        line = _clean(" ".join(text_lines))
        words = line.split()
        if not words:
            continue
        span = max(end - start, 0.0)
        total = sum(len(w) for w in words) or 1
        acc = 0
        for w in words:
            ws = start + (acc / total) * span
            acc += len(w)
            we = start + (acc / total) * span
            tokens.append(Token(w, ws, we))
    return tokens


# --------------------------------------------------------------------- auto
def _tokens_auto(cues):
    """Reconstruct per-word timing, dropping the rolling format's repeats.

    `prev_words` is the last line we emitted; a new cue's line usually shares a
    prefix with it (a line growing word-by-word) or shares nothing (a fresh
    line) -- either way we only emit the novel suffix, so nothing repeats."""
    tokens = []
    prev_words = []
    for cs, ce, text_lines in cues:
        timed_lines = [l for l in text_lines if _INLINE.search(l)]
        if timed_lines:
            for line in timed_lines:
                pairs = _timed_words(line, cs)         # [(word, start), ...]
                words = [w for w, _ in pairs]
                k = _common_prefix(words, prev_words)
                for w, t in pairs[k:]:
                    tokens.append(Token(w, t, None))
                if words:
                    prev_words = words
            continue
        # No inline timing: a settled-plain cue. Emit only genuinely new text
        # (e.g. a single-word line like "anjing" that never carried tags).
        for raw in text_lines:
            words = _clean(raw).split()
            if not words or words == prev_words:
                continue
            k = _common_prefix(words, prev_words)
            for w in words[k:]:
                tokens.append(Token(w, cs, None))
            prev_words = words
    return tokens


def _timed_words(line, cue_start):
    """[(word, start_seconds)] for one auto-caption line. The leading segment
    before the first inline tag is spoken at the cue's own start."""
    stripped = _CTAG.sub("", line)
    parts = _INLINE.split(stripped)   # [lead, ts, txt, ts, txt, ...]
    out = []
    for w in _clean(parts[0]).split():
        out.append((w, cue_start))
    idx = 1
    while idx + 1 < len(parts):
        t = _secs(parts[idx])
        for w in _clean(parts[idx + 1]).split():
            out.append((w, t))
        idx += 2
    return out


def _common_prefix(a, b):
    k = 0
    while k < len(a) and k < len(b) and a[k].casefold() == b[k].casefold():
        k += 1
    return k


# ------------------------------------------------------------------- helpers
def _fill_ends(tokens):
    """Set each word's end to the next word's start (last word gets a default),
    clamped so a window is never inverted."""
    filled = []
    for i, tok in enumerate(tokens):
        if tok.end is not None:
            end = tok.end
        elif i + 1 < len(tokens):
            end = tokens[i + 1].start
        else:
            end = tok.start + _LAST_DUR
        end = max(end, tok.start + _MIN_DUR)
        filled.append(Token(tok.text, tok.start, end))
    return filled


def _clean(s):
    """Strip inline tags and bracket noise; normalize NBSP to a space."""
    s = _CTAG.sub("", s)
    s = _INLINE.sub("", s)
    s = _BRACKET.sub("", s)
    return s.replace("\u00a0", " ").strip()


def _secs(s):
    m = _TS.search(s)
    if not m:
        return 0.0
    h, mm, ss, ms = m.groups()
    return int(h) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0
