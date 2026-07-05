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
