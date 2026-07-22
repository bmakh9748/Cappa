"""Unit test: the KANJIDIC2 pack — per-character info for the Grammar tab.

Needs the pack (~1.25 MB, downloaded once into jmdict_packs/). Without it
the pack-gated half SKIPS, like test_jmdict."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cappa.language.japanese import kanjidic

# ---- gating: only "ja" ever downloads anything --------------------------
assert kanjidic.ensure_pack("ar") is False
assert kanjidic.ensure_pack(None) is False
print("PASS kanjidic: non-Japanese languages never fetch the pack")

if not kanjidic.ensure_pack("ja", timeout=180.0):
    print("SKIP: no KANJIDIC2 pack (offline?)")
    sys.exit(0)

# ---- the probe's ground truth for 面 and 倒 ------------------------------
men = kanjidic.kanji_info("面")
assert men is not None
assert "face" in men.meanings and "mask" in men.meanings, men.meanings
assert "メン" in men.onyomi and "おもて" in men.kunyomi
assert men.strokes == 9 and men.grade == 3 and men.jlpt == 2, (
    men.strokes, men.grade, men.jlpt)
tou = kanjidic.kanji_info("倒")
assert tou is not None and "fall" in tou.meanings
assert tou.grade == 8, tou.grade    # secondary jōyō, not a school grade
print("PASS kanjidic: 面 and 倒 answer with meanings, readings, stats")

# ---- non-kanji resolve to nothing, quietly ------------------------------
for ch in ("の", "a", "7", "", "ハ"):
    assert kanjidic.kanji_info(ch) is None, ch
print("PASS kanjidic: kana/latin/digits have no kanji card")

# ---- breakdown: unique characters, reading order, kana skipped ----------
got = [k.literal for k in kanjidic.breakdown("公衆電話")]
assert got == ["公", "衆", "電", "話"], got
got = [k.literal for k in kanjidic.breakdown("食べられる")]
assert got == ["食"], got
assert kanjidic.breakdown("") == []
# A word repeating a kanji teaches it once (人々 uses the iteration mark,
# so spell it out).
got = [k.literal for k in kanjidic.breakdown("人人")]
assert got == ["人"], got
print("PASS kanjidic: breakdown walks the headword's unique kanji")

print("\nALL PASS")
