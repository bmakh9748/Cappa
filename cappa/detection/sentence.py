"""The data model a recognised caption line becomes: a Sentence of Words.

Detection produces these (ocr.py builds them, the ledger keeps them per
live caption), the UI consumes them: each hotspot IS a Word, so a click
hands the popup a Word that already knows its text, its place on screen,
and — through .sentence — the full line it came from. That is exactly the
payload the popup's meaning lookup (and the Anki card) needs.

The Japanese word-unit decision landed here (2026-07-09). For SPACED
scripts a Word is still a word. For CJK a Word is one CHARACTER, because
nothing at OCR time knows where a Japanese word ends: the recogniser's own
kanji-run/kana-run grouping cuts at the okurigana boundary, tearing 戻る
into 戻 | るのも. The word is found at LOOKUP time instead — `cappa.jmdict`
resolves the character under the cursor to the whole word it belongs to —
and `span_word()` fuses that character range back into one Word for the
popup and the card. Without a dictionary pack `script_span()` reproduces
the old kanji/kana grouping, so nothing gets worse.

Plain data, no Qt. Boxes are (l, t, r, b) region-local physical px."""


def is_cjk(ch):
    """A character written without spaces around it."""
    o = ord(ch)
    return (0x3040 <= o <= 0x30FF        # hiragana + katakana
            or 0x3400 <= o <= 0x9FFF     # CJK ideographs
            or 0xF900 <= o <= 0xFAFF     # compatibility ideographs
            or 0xFF66 <= o <= 0xFF9F)    # halfwidth katakana


def _script_class(ch):
    o = ord(ch)
    if 0x3040 <= o <= 0x309F:
        return "hiragana"
    if 0x30A0 <= o <= 0x30FF or 0xFF66 <= o <= 0xFF9F:
        return "katakana"
    if 0x3400 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF:
        return "kanji"
    return "other"


def script_span(text, index):
    """(start, end) of the run of same-script characters around `index`.

    The fallback when no dictionary can say where the word ends — it is the
    grouping the recogniser used to do, and it is wrong at exactly the
    okurigana boundary. Only used when the JMdict pack is absent or has
    nothing for this character."""
    if not text or not (0 <= index < len(text)):
        return (index, index + 1)
    want = _script_class(text[index])
    start = index
    while start > 0 and _script_class(text[start - 1]) == want:
        start -= 1
    end = index + 1
    while end < len(text) and _script_class(text[end]) == want:
        end += 1
    return (start, end)


class Word:
    __slots__ = ("text", "box", "sentence", "index", "lemma")

    def __init__(self, text, box, sentence, index=-1, lemma=None):
        self.text = text
        self.box = box
        self.sentence = sentence  # the Sentence this word belongs to
        # Character offset of `text` within sentence.text (-1 if unknown).
        # For CJK this is what the dictionary lookup scans from.
        self.index = index
        # The dictionary form, when a lookup resolved one (戻って -> 戻る).
        # None for a plain word; the card studies the lemma, the screen and
        # the provenance check keep the surface.
        self.lemma = lemma

    def __repr__(self):
        return "Word(%r, %r)" % (self.text, self.box)


def span_word(sentence, start, end):
    """One Word covering characters [start, end) of `sentence`.

    The clicked word, fused back together out of the per-character hotspots
    the dictionary lookup spanned. Its box is their union, so the highlight
    and the card's word_box cover the whole word."""
    inside = [w for w in sentence.words
              if w.index >= 0 and start <= w.index < end]
    if not inside:
        return None
    boxes = [w.box for w in inside]
    box = (min(b[0] for b in boxes), min(b[1] for b in boxes),
           max(b[2] for b in boxes), max(b[3] for b in boxes))
    return Word(sentence.text[start:end], box, sentence, index=start)


class Sentence:
    __slots__ = ("text", "box", "words", "appeared_at", "cleared_at",
                 "ocr_conf", "junk")

    def __init__(self, text, box, word_spans):
        """word_spans: [(word_text, word_box), ...] left to right."""
        self.text = text
        self.box = box
        self.words = []
        cursor = 0
        for t, b in word_spans:
            # Where this hotspot's text sits in the line. Found forward from
            # the last one, since _respace may have put spaces between them.
            at = text.find(t, cursor) if t else -1
            if at < 0:
                at = cursor
            self.words.append(Word(t, b, self, index=at))
            cursor = at + max(len(t), 1)
        # Recognition confidence of the read that produced this line (0-1),
        # or None when unknown. A card built from a shaky read says so
        # (card_0060: an unsupported page read at conf 0.45 made a
        # confident-looking garbage card).
        self.ocr_conf = None
        # Why the text rules call this row junk (a watermark handle, a clock,
        # a URL), or None. Set by the worker; junk stays CLICKABLE but must
        # not let onto a card: a stamped row never joins a caption block
        # (below) and never enters the transcript.
        self.junk = None
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
    Always contains `sentence` -- even a junk one, since clicking it is a
    deliberate act; but a junk row NEVER joins someone else's block
    (card_0028: the channel's '@korrathetaymi' watermark sits a row above
    the caption, matches its glyph height, and became part of the card's
    sentence)."""
    block = [sentence]
    pool = [s for s in (captions or [])
            if s is not sentence and getattr(s, "words", None)
            and not getattr(s, "junk", None)]
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
    def ocr_conf(self):
        """The block reads as its LEAST confident line: one garbled row
        poisons the joined sentence just as much as all of them."""
        confs = [s.ocr_conf for s in self.lines
                 if getattr(s, "ocr_conf", None) is not None]
        return min(confs) if confs else None

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
