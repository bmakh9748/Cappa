"""Word-at-a-time sentence completion (user spec, 2026-07-09): the clicked
chunk finds its whole sentence in the caption track; OUR transcript's words
always come first, the track only reveals the sentence extends and fills the
words OCR missed; low-confidence rows lose to the track; timings from our
transcript where present, the track's otherwise. Data mirrors the real
q86axsLk_qQ transcript ('RESPECT' clicked while 'I don't respect you at all'
was spoken)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cappa.flashcard.builder import (_boxes_overlap, _complete_sentence,
                                     _fill_sentence)
from cappa.flashcard.model import CardDraft
from cappa.source.transcript import Transcript, sentence_slice
from cappa.source.vtt import Token

# The track: two punctuated sentences plus a '>>' speaker marker.
TOKS = [
    Token("You're", 7.9, 8.1), Token("not", 8.1, 8.4), Token("my", 8.4, 8.7),
    Token("blood.", 8.7, 9.4),
    Token("&gt;&gt;", 9.4, 9.5),
    Token("I", 9.5, 9.6), Token("don't", 9.6, 9.9),
    Token("respect", 9.9, 10.2), Token("you", 10.2, 10.5),
    Token("at", 10.5, 10.7), Token("all.", 10.7, 11.2),
    Token("I", 12.0, 12.2), Token("can't", 12.2, 12.6),
]

# sentence_slice: the matched 'respect' token grows to its whole sentence —
# stopping at the previous sentence's period/'>>' and at its own period.
i0, j0, capped = sentence_slice(TOKS, 7, 8)   # [respect]
assert (i0, j0) == (5, 11), (i0, j0)          # just after '>>' .. 'all.'
assert capped is False, "a punctuated sentence is not a runaway"
tr = Transcript(TOKS)
sent = tr.sentence_for("RESPECT", near_t=9.9)
assert sent is not None
assert [w[0] for w in sent["words"]] == ["I", "don't", "respect", "you",
                                         "at", "all."], sent
assert abs(sent["start"] - 9.5) < 1e-9 and abs(sent["end"] - 11.2) < 1e-9
print("PASS: a matched chunk grows to its punctuated track sentence")

# A big silence splits sentences even without punctuation.
FLAT = [Token("satu", 1.0, 1.2), Token("dua", 1.2, 1.5),
        Token("tiga", 3.6, 3.9), Token("empat", 3.9, 4.2)]   # 2.1s gap
assert sentence_slice(FLAT, 0, 1) == (0, 2, False)
assert sentence_slice(FLAT, 2, 3) == (2, 4, False)
print("PASS: without punctuation, a big time split is the boundary")

# The runaway: continuous unpunctuated auto-caption speech with no gaps grows
# past SENTENCE_MAX, so `capped` is True and sentence_for refuses to complete
# (card_0032: a clean 7-word line otherwise ballooned into the first 15s).
RUN = [Token("w%d" % k, k * 0.5, k * 0.5 + 0.4) for k in range(40)]  # 0..20s
i0, j0, capped = sentence_slice(RUN, 6, 8)
assert capped is True, "a boundary-less 20s run should read as a runaway"
assert Transcript(RUN).sentence_for("w6 w7", near_t=3.5) is None, (
    "a runaway track sentence must not complete")
print("PASS: a boundary-less auto-caption run is refused, not merged")

# Burned-in translation subs don't match the track: no sentence, no touch.
assert tr.sentence_for("KATA YANG BERBEDA SEKALI", near_t=9.9) is None
print("PASS: text that doesn't match the track is left alone (the gate)")


def _row(text, a, c, conf=0.96):
    return {"text": text, "appeared_video": a, "cleared_video": c,
            "ocr_conf": conf}


# The merge, on the real failure: OCR caught fragments; the track knows the
# whole sentence. OUR words win where we have them; the track fills 'you'.
rows = [_row("I DON'T", 9.61, 9.77), _row("RESPECT", 9.91, 10.17),
        _row("AT ALL", 10.55, 10.82)]                  # 'you' never read
got = _fill_sentence(rows, sent)
assert got["text"] == "I DON'T RESPECT you AT ALL", got
assert abs(got["start"] - 9.61) < 1e-9, got     # ours: first row's appear
assert abs(got["end"] - 10.82) < 1e-9, got      # ours: last row's clear
assert got["filled"] == 1, got
print("PASS: ours-first merge; the track fills only what OCR missed")

# A low-confidence row loses its say: the track speaks there instead.
rows2 = [_row("I DON'T", 9.61, 9.77), _row("RESPCT", 9.91, 10.17, conf=0.6),
         _row("YOU", 10.22, 10.39), _row("AT ALL", 10.55, 10.82)]
got2 = _fill_sentence(rows2, sent)
assert got2["text"] == "I DON'T respect YOU AT ALL", got2
print("PASS: a low-confidence read is replaced by the track's word")

# A re-read of the same sighting joins once; edges fall back to the track
# when no row covers them.
rows3 = [_row("RESPECT", 9.91, 10.17), _row("RESPECT", 9.95, 10.2)]
got3 = _fill_sentence(rows3, sent)
assert got3["text"] == "I don't RESPECT you at all.", got3
assert abs(got3["start"] - 9.5) < 1e-9, got3    # track: no row at the start
assert abs(got3["end"] - 11.2) < 1e-9, got3     # track: no row at the end
print("PASS: re-reads join once; uncovered edges take the track's timing")

# All rows low-confidence -> nothing of ours -> no assembly at all.
assert _fill_sentence([_row("REPSECT", 9.91, 10.17, conf=0.5)], sent) is None
print("PASS: with nothing of ours trusted, no sentence is invented")

# The title-bleed guard (card_0031): a persistent on-screen TITLE row whose
# only overlap with the spoken sentence is one fuzzy word must NOT splice
# itself in. 'Watch Over And Over Again' shares only 'watch'~'what' (0.67) —
# five words, one match, below the sentence-level bar of three. It is dropped,
# and the 'what' it grabbed is filled back from the track, not lost.
title_sent = {"words": [("say", 2.4, 2.6), ("what", 2.6, 2.8),
                        ("the", 2.8, 2.9), ("crap", 2.9, 3.3)]}
title_rows = [_row("crap", 2.9, 3.3),                       # the clicked word
              _row("Watch Over And Over Again", 0.5, 3.5)]  # persistent title
gotT = _fill_sentence(title_rows, title_sent)
assert gotT is not None
assert "Watch" not in gotT["text"] and "Over" not in gotT["text"], gotT
assert "what" in gotT["text"].split(), gotT     # the stolen word filled back
print("PASS: a persistent title sharing one word is kept off the sentence")


# ---- builder-level: _complete_sentence end to end with a stub source ----
class _StubSource:
    def __init__(self, tr, rows):
        self._tr, self._rows = tr, rows

    def sentence_for(self, text, near_t=None):
        return self._tr.sentence_for(text, near_t=near_t)

    def rows_between(self, t0, t1):
        return [r for r in self._rows
                if r["cleared_video"] >= t0 and r["appeared_video"] <= t1]


class _StubSentence:
    ocr_conf = 0.96


draft = CardDraft("RESPECT", "RESPECT")
draft.word_index = 0
src = _StubSource(tr, [_row("I DON'T", 9.61, 9.77),
                       _row("AT ALL", 10.55, 10.82)])
_complete_sentence(draft, _StubSentence(), src, near_t=9.95)
assert draft.sentence == "I DON'T RESPECT you AT ALL", draft.sentence
assert draft.word_index == 2, draft.word_index          # recomputed
assert draft.assembled and draft.assembled["filled_from_track"] == 1
assert draft.assembled["ocr_sentence"] == "RESPECT"
assert abs(draft.assembled["start"] - 9.61) < 1e-9
assert any("completed" in n for n in draft.notes), draft.notes
print("PASS: the clicked one-word card grows to its full spoken sentence")

# ...and a sentence that already covers the track's is left untouched.
draft2 = CardDraft("blood", "You're not my blood")
draft2.word_index = 3
src2 = _StubSource(tr, [])
_complete_sentence(draft2, _StubSentence(), src2, near_t=8.5)
assert draft2.sentence == "You're not my blood", draft2.sentence
assert draft2.assembled is None
print("PASS: a complete sentence is not rewritten")

# ---- the spatial gate: a row elsewhere on screen is not part of the sentence
# _boxes_overlap: confirmed non-overlap is the ONLY exclusion; unknown = keep.
assert _boxes_overlap((100, 500, 400, 540), [100, 500, 260, 540])   # same band
assert not _boxes_overlap((100, 500, 400, 540), [120, 40, 500, 80])  # title band
assert _boxes_overlap((100, 500, 400, 540), None)   # unknown box -> fail open
print("PASS: box overlap excludes only a confirmed different screen region")


class _CannedSource:
    """A source whose sentence_for is fixed, so the spatial gate is tested
    without leaning on the aligner."""
    def __init__(self, sent, rows):
        self._sent, self._rows = sent, rows

    def sentence_for(self, text, near_t=None):
        return self._sent

    def rows_between(self, t0, t1):
        return list(self._rows)


class _BottomSentence:
    ocr_conf = 0.96
    box = (100, 500, 300, 540)   # the clicked caption sits near the bottom


# A stray OCR row 'pbyed' up in a TITLE band matches the spoken 'played'
# (0.73) and, ungated, would overwrite it with our misread. Its box does not
# overlap the clicked caption, so the gate drops it and the track's own
# 'played' stands.
CANNED = {"start": 1.0, "end": 2.3, "score": 0.9, "match_start": 1.0,
          "match_end": 1.4,
          "words": [("the", 1.0, 1.2), ("ball", 1.2, 1.4), ("was", 1.4, 1.6),
                    ("played", 1.6, 2.0), ("well", 2.0, 2.3)]}
stray = {"text": "pbyed", "appeared_video": 1.6, "cleared_video": 2.0,
         "ocr_conf": 0.96, "box": [100, 40, 260, 80]}      # title band, up top
draft_sp = CardDraft("ball", "the ball")
draft_sp.word_index = 1
_complete_sentence(draft_sp, _BottomSentence(),
                   _CannedSource(CANNED, [stray]), near_t=1.3)
assert "pbyed" not in draft_sp.sentence, draft_sp.sentence
assert "played" in draft_sp.sentence.split(), draft_sp.sentence
print("PASS: a text-matching row in a different screen band is excluded")

print("ALL PASS")
