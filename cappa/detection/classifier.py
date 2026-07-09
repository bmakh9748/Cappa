"""The text junk tag: is this row's TEXT junk (a clock, a URL, a handle)?

Detection accepts EVERY text line the detector finds (user call, 2026-07-09:
the caption-vs-not geometry gates — size/aspect/position/burst/baseline —
rejected too many real words and were deleted; hover-only styling makes loose
detection cheap, junk text is merely hoverable). Nothing is rejected here
either: the verdict only STAMPS a row (`Sentence.junk`) so a watermark stays
clickable but never joins a caption block, a card sentence, or the OCR
transcript (card_0028: '@korrathetaymi' read at confidence 1.000 joined
'DIED ON THE' as one sentence).

FAIL-OPEN on purpose: a junk verdict needs POSITIVE evidence read with high
confidence. Empty, low-confidence or unreadable text passes — captions in
scripts the recogniser can't read must never regress (Japanese worked before
OCR landed). No Qt: plain, testable."""

MIN_READ_CONF = 0.75     # below this the reading is a guess, not evidence
MIN_LETTER_RATIO = 0.3   # letters (any script) / characters; lower = UI junk
_URL_HINTS = ("www.", "http", ".com", ".net", ".org", ".tv", ".gg")


def text_verdict(text, confidence):
    """None = the text reads like caption text, else why it's junk.
    `str.isalpha()` counts kana/kanji/hangul/…, so non-Latin captions pass
    the letter-ratio gate exactly like English ones."""
    if not text or confidence < MIN_READ_CONF:
        return None
    compact = "".join(text.split()).lower()
    if not compact:
        return None
    letters = sum(ch.isalpha() for ch in compact)
    if letters / len(compact) < MIN_LETTER_RATIO:
        return "junk text %s" % ascii(text)   # clock, score, "1080p60"
    if any(h in compact for h in _URL_HINTS) or compact.startswith("@"):
        return "url/handle %s" % ascii(text)  # watermarks, channel plugs
    return None
