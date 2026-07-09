"""cappa/lexicon.py — the word-list splitter. A tiny synthetic pack written
to a temp dir stands in for a downloaded language pack; no network."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cappa import lexicon


def _with_pack(lang, words):
    """Load `words` (most-common-first) as `lang`'s pack, bypassing the
    disk. Returns a cleanup callable."""
    lexicon._packs[lang] = {w.casefold(): i for i, w in enumerate(words)}

    def restore():
        lexicon._packs.pop(lang, None)
    return restore


# A pack read from a real 'word count' file, most-common first.
with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "en.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("you 999\ncan 500\nalways 200\nfamily 100\nmembers 90\n"
                # pad past the _MIN_VOCAB floor with throwaway entries
                + "".join("w%d 1\n" % i for i in range(lexicon._MIN_VOCAB)))
    ranks = lexicon._read_pack(path)
    assert ranks is not None and ranks["you"] == 0 and ranks["can"] == 1
    print("PASS lexicon: a frequency file parses to word -> rank")

# A pack under the floor is treated as no pack (a partial download).
with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "en.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("you 1\ncan 1\n")
    assert lexicon._read_pack(path) is None
    print("PASS lexicon: a too-small pack is rejected (partial download)")

# split(): glued runs break into real words; words and errors are spared.
restore = _with_pack("en", ["you", "can", "always", "family", "members",
                            "down", "syndrome", "a", "the"])
try:
    assert lexicon.split("CANALWAYS", "en") == ["CAN", "ALWAYS"]
    assert lexicon.split("YOUCANALWAYS", "en") == ["YOU", "CAN", "ALWAYS"]
    assert lexicon.split("FAMILYMEMBERS", "en") == ["FAMILY", "MEMBERS"]
    # A real word is never split, even if sub-words exist ('members' alone).
    assert lexicon.split("MEMBERS", "en") == ["MEMBERS"]
    # No all-known segmentation -> unchanged (OCR error, or unknown word).
    assert lexicon.split("YOUCAL", "en") == ["YOUCAL"]
    assert lexicon.split("XYZZY", "en") == ["XYZZY"]
    # Cap at MAX_PIECES: 'downsyndrome' is two, not four one-letter shards.
    assert lexicon.split("DOWNSYNDROME", "en") == ["DOWN", "SYNDROME"]
    # A one-letter piece is refused even when it's a known word ('a').
    assert lexicon.split("ACAN", "en") == ["ACAN"]
    # known()/case handling.
    assert lexicon.known("Always", "en") is True
    assert lexicon.known("nope", "en") is False
    print("PASS lexicon: splits glued runs, spares words, errors and shards")
finally:
    restore()

# Real-pack junk: FrequencyWords (OpenSubtitles) lists noise like 'ee', 'dd',
# 'jun', 'eee' as 'words' at high ranks, so the splitter once tore the on-
# screen name 'JUNEEEEDD' into 'june eee dd'. A piece that is one repeated
# letter, or short AND rare, is not a word to split on — but a real split of
# common words still goes through.
lexicon._packs["en"] = {"you": 0, "can": 1, "always": 200,
                        "june": 14000, "jun": 7000, "ee": 13000,
                        "eee": 26000, "dd": 8000}
try:
    assert lexicon.split("JUNEEEEDD", "en") == ["JUNEEEEDD"], \
        lexicon.split("JUNEEEEDD", "en")
    assert lexicon.split("JUNEEDD", "en") == ["JUNEEDD"], \
        lexicon.split("JUNEEDD", "en")
    assert lexicon.split("CANALWAYS", "en") == ["CAN", "ALWAYS"]  # real split OK
finally:
    lexicon._packs.pop("en", None)
print("PASS lexicon: a name is not torn into junk short 'words'")

# No pack for a language: split is a no-op and known() says 'don't know'.
assert lexicon.split("CANALWAYS", "de") == ["CANALWAYS"]
assert lexicon.known("hund", "de") is None
# CJK-style / unsupported languages are never packed.
assert lexicon.split("SOMETHING", "ja") == ["SOMETHING"]
print("PASS lexicon: no pack -> no splitting, and unknown means unknown")

# Ranking: prefer the segmentation of commoner words. With both 'to'+'get'
# and 'together' impossible here, set up a genuine tie-break.
restore = _with_pack("en", ["to", "get", "her", "together", "tog", "ether"])
try:
    # 'togetherx' isn't a word; 'to'+'get'+'her' (all common, ranks 0-2)
    # beats 'tog'+'ether' (ranks 4-5).
    assert lexicon.split("TOGETHER", "en") == ["TOGETHER"]   # it's a word
    assert lexicon.split("TOGETHERX", "en") == ["TOGETHERX"]  # x breaks it
    print("PASS lexicon: a known whole word wins over any split")
finally:
    restore()

print("ALL PASS")
