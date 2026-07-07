"""The data model a recognised caption line becomes: a Sentence of Words.

Detection produces these (ocr.py builds them, the ledger keeps them per
live caption), the UI consumes them: each hotspot IS a Word, so a click
hands the popup a Word that already knows its text, its place on screen,
and — through .sentence — the full line it came from. That is exactly the
payload the popup's translation (and the Anki card later) needs, and the
single place to change when the Japanese word-unit decision lands (today a
Word is the recogniser's grouping: real words for spaced scripts, kanji/
kana runs for CJK; a tokeniser would swap in here without touching the UI).

Plain data, no Qt. Boxes are (l, t, r, b) region-local physical px."""


class Word:
    __slots__ = ("text", "box", "sentence")

    def __init__(self, text, box, sentence):
        self.text = text
        self.box = box
        self.sentence = sentence  # the Sentence this word belongs to

    def __repr__(self):
        return "Word(%r, %r)" % (self.text, self.box)


class Sentence:
    __slots__ = ("text", "box", "words", "appeared_at", "cleared_at")

    def __init__(self, text, box, word_spans):
        """word_spans: [(word_text, word_box), ...] left to right."""
        self.text = text
        self.box = box
        self.words = [Word(t, b, self) for t, b in word_spans]
        # Wall-clock (time.monotonic) when detection first accepted this line
        # and when its clear was noticed — the anchors the flashcard's audio
        # clip is cut from. 0.0 until set by the ledger. cleared_at stays 0.0
        # while the line is still on screen. The clicked Word reaches these
        # through .sentence, and because the ledger mutates THIS object, a
        # popup already holding the Word sees cleared_at fill in later.
        self.appeared_at = 0.0
        self.cleared_at = 0.0

    def __iter__(self):
        return iter(self.words)

    def __len__(self):
        return len(self.words)

    def __repr__(self):
        return "Sentence(%r, %d words)" % (self.text, len(self.words))


# Two OCR lines are one caption BLOCK when they are stacked rows of the same
# rendered subtitle: about the same glyph height, directly above/below each
# other, and horizontally aligned. Other live text on screen (a chat line, a
# HUD label) fails the adjacency or alignment test.
BLOCK_HEIGHT_RATIO = 1.6   # max glyph-height disagreement between rows
BLOCK_ROW_GAP = 0.9        # max vertical gap between rows, × min row height
BLOCK_X_OVERLAP = 0.5      # min horizontal overlap, × the narrower row
BLOCK_MAX_LINES = 3        # subtitles never render more rows than this; a
                           # taller stack means something else (a chat
                           # column) chained in and must be cut back
BLOCK_ROW_BLEED = 0.45     # max vertical OVERLAP between rows, × min row
                           # height: outline/glow fonts pad the detector's
                           # boxes past the glyphs, so adjacent rows of one
                           # block genuinely overlap (card_0052: 18px on
                           # 82px-tall rows)
BLOCK_ROW_APART = 0.5      # min centre-to-centre distance, × min row height:
                           # centres closer than this are one row seen twice,
                           # never two stacked rows


def _stacked(a, b):
    """True when lines a and b are adjacent rows of one caption block."""
    ah = a.box[3] - a.box[1]
    bh = b.box[3] - b.box[1]
    if ah <= 0 or bh <= 0:
        return False
    if max(ah, bh) > BLOCK_HEIGHT_RATIO * min(ah, bh):
        return False
    overlap = min(a.box[2], b.box[2]) - max(a.box[0], b.box[0])
    if overlap < BLOCK_X_OVERLAP * min(a.box[2] - a.box[0],
                                       b.box[2] - b.box[0]):
        return False
    if b.box[1] >= a.box[3]:        # b below a
        gap = b.box[1] - a.box[3]
    elif a.box[1] >= b.box[3]:      # b above a
        gap = a.box[1] - b.box[3]
    else:
        # Overlapping boxes are still stacked rows when the overlap is the
        # font's outline/glow bleeding one row's box into the next
        # (card_0052: the top line's box ended 18px below the bottom line's
        # start, the block was refused, and the card kept half its
        # caption). Shallow bleed with the centres a clear row apart is a
        # stack; anything deeper is one row seen twice, or text drawn over
        # text — not a block.
        bleed = min(a.box[3], b.box[3]) - max(a.box[1], b.box[1])
        if bleed > BLOCK_ROW_BLEED * min(ah, bh):
            return False
        apart = abs((a.box[1] + a.box[3]) - (b.box[1] + b.box[3])) / 2.0
        if apart < BLOCK_ROW_APART * min(ah, bh):
            return False
        gap = 0
    return gap <= BLOCK_ROW_GAP * min(ah, bh)


def caption_block(sentence, captions):
    """The stacked caption block `sentence` belongs to, as [Sentence] in
    reading order (top row first). Detection keeps one Sentence per text
    LINE, so a two-line subtitle is two Sentences and a card made from
    either would carry only half the caption (card_0031). Grows
    transitively, so a three-line caption joins through its middle row.
    Always contains `sentence`."""
    block = [sentence]
    pool = [s for s in (captions or [])
            if s is not sentence and getattr(s, "words", None)]
    grew = True
    while grew:
        grew = False
        for cand in list(pool):
            if any(_stacked(line, cand) for line in block):
                block.append(cand)
                pool.remove(cand)
                grew = True
    if len(block) > BLOCK_MAX_LINES:
        # Keep the clicked line and its nearest rows.
        mid = (sentence.box[1] + sentence.box[3]) / 2.0
        block.sort(key=lambda s: abs((s.box[1] + s.box[3]) / 2.0 - mid))
        block = block[:BLOCK_MAX_LINES]
    block.sort(key=lambda s: s.box[1])
    return block


def click_pool(snapshot, current, clicked):
    """The candidate lines for a card's caption block, reconciling the two
    moments we saw them. `snapshot` is the live list frozen at CLICK time —
    it exists because the caption may clear while the popup sits open.
    `current` is the live list at CARD time — it exists because detection
    may still have been churning at the click: card_0045 was clicked in the
    half-second the ledger spent re-reading the top line of a fresh
    two-liner, so the snapshot held only the clicked line and the card lost
    the line above (which was plainly on screen in the click screenshot).

    While the clicked line is still live, `current` is the base — a sibling
    that finished detection after the click is in there — and snapshot lines
    keep only rows no live line occupies (a sibling that truly cleared while
    the popup was open still makes the card). Once the clicked line is gone,
    the screen has moved on and only the snapshot can be trusted."""
    snap = list(snapshot or [])
    cur = list(current or [])
    if not any(s is clicked for s in cur):
        return snap
    for line in snap:
        if not any(_same_row(line.box, c.box) for c in cur):
            cur.append(line)
    return cur


def _same_row(a, b):
    """True when boxes a and b occupy the same text row: vertical centres
    nearly coincide and they overlap horizontally — one caption line seen
    twice (before/after a re-read), not two stacked rows."""
    ah = a[3] - a[1]
    bh = b[3] - b[1]
    if ah <= 0 or bh <= 0:
        return False
    if abs((a[1] + a[3]) - (b[1] + b[3])) / 2.0 >= 0.8 * min(ah, bh):
        return False
    overlap = min(a[2], b[2]) - max(a[0], b[0])
    return overlap >= 0.5 * min(a[2] - a[0], b[2] - b[0])


class CaptionBlock:
    """A multi-line caption as ONE sentence: the lines of a caption_block
    joined top-to-bottom. Quacks like Sentence everywhere the card builder
    reads one (text / box / words / appear-clear times) while the per-line
    Sentences stay untouched in the ledger. The words are the lines' own
    Word objects, so identity checks against a clicked Word still hold.
    Appear/clear times pass through LIVE from the clicked line, because the
    ledger mutates that object while a popup sits open."""

    def __init__(self, lines, clicked):
        self.lines = list(lines)
        self._clicked = clicked
        self.text = " ".join(s.text for s in self.lines if s.text)
        boxes = [s.box for s in self.lines]
        self.box = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                    max(b[2] for b in boxes), max(b[3] for b in boxes))
        self.words = [w for s in self.lines for w in s.words]

    @property
    def appeared_at(self):
        return self._clicked.appeared_at

    @property
    def cleared_at(self):
        return self._clicked.cleared_at

    def __iter__(self):
        return iter(self.words)

    def __len__(self):
        return len(self.words)

    def __repr__(self):
        return "CaptionBlock(%r, %d lines)" % (self.text, len(self.lines))
