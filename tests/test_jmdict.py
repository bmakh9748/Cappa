"""Japanese word lookup: deinflection, longest match, and the word_at scan.

Ground truth is the user's own screenshot (a Persona 5 clip, 2026-07-09) —
the lines whose script-run hotspots came apart as 戻 | るのも and produced a
card for the fragment 戻. Every assertion below is a click that used to be
wrong.

Needs the JMdict pack (~14 MB, downloaded once into jmdict_packs/). Without
it the whole file SKIPS: the pack is a runtime download like the OCR models,
not something a checkout carries.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cappa import jmdict
from cappa.detection.sentence import (Sentence, is_cjk, script_span,
                                      selection_word, span_word)

# ---- pure, no pack needed: the deinflection engine ---------------------
forms = {f for f, _r, _t in jmdict._deinflect("戻って")}
assert "戻る" in forms, forms
forms = {f for f, _r, _t in jmdict._deinflect("食べた")}
assert "食べる" in forms, forms
# The five-rule chain: やっといてくれ -> やっといてくる -> やっといて ->
# やっとく -> やって -> やる.
forms = {f for f, _r, _t in jmdict._deinflect("やっといてくれ")}
assert "やる" in forms, sorted(forms)[:20]
# Katakana emphasis is folded to the kana JMdict keys words under.
assert jmdict._to_hiragana("オマエ") == "おまえ"
assert jmdict._to_hiragana("戻るノ") == "戻るの"
# Old-form kanji fold to the modern spellings JMdict keys (card_0002: a
# brush font drew 拔, the old form of 抜, and the whole idiom missed).
assert jmdict._fold_old_kanji("拔山蓋世") == "抜山蓋世"
assert jmdict._fold_old_kanji("學校圖書館") == "学校図書館"
assert jmdict._fold_old_kanji("戻る") == "戻る"        # modern text untouched
assert "抜山蓋世" in {f for f, _r, _t in jmdict._deinflect("拔山蓋世")}
print("PASS: deinflection unwinds te-forms, contractions, katakana and "
      "old kanji")

# ---- the script-run fallback IS the old, wrong grouping ----------------
# Kept only for when no pack is present; the test pins what it does so the
# fallback is never mistaken for a fix.
assert script_span("戻るのも面倒なんで", 0) == (0, 1)      # 戻 alone
assert script_span("戻るのも面倒なんで", 1) == (1, 4)      # るのも
assert is_cjk("戻") and is_cjk("る") and not is_cjk("a")
assert not is_cjk("한")   # hangul IS spaced: never per-character hotspots
print("PASS: script_span reproduces the kanji/kana grouping it replaced")

# ---- Sentence gives every hotspot its character offset -----------------
line = "戻るのも面倒なんで"
spans = [(ch, (i * 40, 0, i * 40 + 40, 44)) for i, ch in enumerate(line)]
sentence = Sentence(line, (0, 0, len(line) * 40, 44), spans)
assert [w.index for w in sentence.words] == list(range(len(line)))
# ...and for a spaced line the offsets skip the spaces.
spaced = Sentence("hello world", (0, 0, 200, 44),
                  [("hello", (0, 0, 90, 44)), ("world", (100, 0, 190, 44))])
assert [w.index for w in spaced.words] == [0, 6]

fused = span_word(sentence, 4, 6)          # 面倒
assert fused.text == "面倒" and fused.index == 4
assert fused.box == (160, 0, 240, 44), fused.box   # union of both characters
print("PASS: Word carries its char offset; span_word fuses a character range")

# ---- selection_word: exactly what a drag swept --------------------------
sel = selection_word(sentence, sentence.words[4], sentence.words[5])
assert sel.text == "面倒", sel.text
# Backwards drags select the same span.
assert selection_word(sentence, sentence.words[5], sentence.words[4]).text \
    == "面倒"
# A single hotspot is a legitimate selection: one character out of the
# longer dictionary word (倒 out of 面倒).
solo = selection_word(sentence, sentence.words[5], sentence.words[5])
assert solo.text == "倒" and solo.box == (200, 0, 240, 44), (solo.text,
                                                             solo.box)
# The span ends at the LAST hotspot's end, not its start index: on spaced
# scripts a hotspot is a whole word, and slicing to its start cut the final
# word to one letter ('hello w').
eng = selection_word(spaced, spaced.words[0], spaced.words[1])
assert eng.text == "hello world", eng.text
print("PASS: selection_word sweeps forward/backward, down to one character")

if not jmdict.ensure_pack("ja", timeout=180.0):
    print("\nSKIP: no JMdict pack (offline?) — dictionary assertions skipped")
    sys.exit(0)

# ---- the screenshot, character by character ----------------------------
# (clicked char, expected surface, expected headword)
CASES = [
    ("戻るのも面倒なんで", 0, "戻る", "戻る"),      # was 戻 alone
    ("戻るのも面倒なんで", 1, "戻る", "戻る"),      # was 'るのも'
    ("戻るのも面倒なんで", 2, "の", "の"),          # was 'るのも'; not 乃
    ("戻るのも面倒なんで", 4, "面倒", "面倒"),
    ("戻るのも面倒なんで", 5, "面倒", "面倒"),      # mid-word: look back
    ("戻るのも面倒なんで", 6, "なんで", "何で"),
    ("戻るのも面倒なんで", 8, "なんで", "何で"),
    ("オマエやっといてくれ。", 0, "オマエ", "お前"),  # katakana emphasis
    # Five deinflection rules deep; headword is the kana やる, because its
    # only kanji spelling 遣る is tagged rare.
    ("オマエやっといてくれ。", 3, "やっといてくれ", "やる"),
    ("何で公衆電話に?", 0, "何で", "何で"),
    ("何で公衆電話に?", 5, "公衆電話", "公衆電話"),  # last char of a compound
    ("わかった", 0, "わかった", "分かる"),          # NOT 分かつ 'to divide'
    ("面倒くさい", 2, "面倒くさい", "面倒くさい"),
    ("本を食べられなかった", 0, "本", "本"),         # 'book', not 元 'origin'
    ("本を食べられなかった", 1, "を", "を"),         # the particle
    ("本を食べられなかった", 2, "食べられなかった", "食べられる"),
]
for text, index, surface, headword in CASES:
    match = jmdict.word_at(text, index)
    assert match is not None, (text, index, "no match")
    assert match.surface == surface, (text, index, match.surface, surface)
    assert match.entry.headword == headword, (
        text, index, match.entry.headword, headword)
print("PASS: %d clicks from the screenshot resolve to the right word"
      % len(CASES))

# 戻る's senses are the ones the reference popup showed.
match = jmdict.word_at("戻るのも面倒なんで", 0)
assert "Godan verb (-ru)" in match.entry.tags(), match.entry.tags()
assert "intransitive" in match.entry.tags()
assert "to turn back" in match.entry.senses[0][1][0]
assert not match.reasons                       # dictionary form already

# An inflected surface reports how it got home.
match = jmdict.word_at("食べられなかった", 0)
assert match.base == "食べられる" and match.reasons, match.reasons
print("PASS: entries carry reading, POS tags, senses and the inflection chain")

# ---- resolve() alone only scans FORWARD; word_at looks back ------------
assert jmdict.resolve("面倒", 1).surface == "倒"      # the lone kanji
assert jmdict.word_at("面倒", 1).surface == "面倒"    # the word it is inside
print("PASS: word_at finds the word a mid-word character belongs to")

# ---- old-form kanji resolve to the modern entry (card_0002) -------------
match = jmdict.word_at("拔山蓋世", 0)
assert match is not None and match.surface == "拔山蓋世", match
assert match.entry.headword == "抜山蓋世", match.entry.headword
assert "strength" in match.entry.senses[0][1][0]
assert jmdict.word_at("拔山蓋世", 2).entry.headword == "抜山蓋世"
assert jmdict.lookup("拔山蓋世")[0].headword == "抜山蓋世"
print("PASS: a brush font's old kanji still find the modern entry")

# ---- the pack's own example sentences (schema 3) ------------------------
# The examples release carries Tatoeba jp/en pairs; 食べる is common enough
# that an empty crop means the build dropped them.
assert jmdict.variant() == "examples", jmdict.variant()
entry = jmdict.lookup("食べる")[0]
assert isinstance(entry.examples, tuple), type(entry.examples)
assert entry.examples, "食べる lost its pack examples"
for jp, en, used in entry.examples:
    assert jp and en, (jp, en)
    assert used == "" or used in jp, (used, jp)   # the form the sentence uses
assert len(entry.examples) <= jmdict.MAX_EXAMPLES
# An entry nothing was ever written about still answers with a tuple.
assert isinstance(jmdict.lookup("抜山蓋世")[0].examples, tuple)
print("PASS: entries carry the pack's jp/en example pairs")

# ---- the offline bridge: an old zip keeps lookup alive across schema 3 --
# A schema-2 machine upgrading OFFLINE must not lose Japanese lookup: its
# old cached zip builds a 'plain' bridge pack, and a later session with
# network upgrades it in place. Miniature synthetic zips keep this instant.
import io
import json
import shutil
import tempfile
import zipfile


def mini_zip(with_examples):
    sense = {"partOfSpeech": ["v5r"],
             "gloss": [{"lang": "eng", "text": "to return"}]}
    if with_examples:
        sense["examples"] = [{
            "text": "戻る",
            "sentences": [{"lang": "jpn", "text": "すぐ戻る。"},
                          {"lang": "eng", "text": "I'll be right back."}],
        }]
    words = [{"kanji": [{"text": "戻る", "common": True, "tags": []}],
              "kana": [{"text": "もどる", "common": True, "tags": []}],
              "sense": [sense]}]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mini.json",
                   json.dumps({"version": "mini", "words": words},
                              ensure_ascii=False))
    return buf.getvalue()


orig_dir = jmdict.PACKS_DIR
orig_download = jmdict._download
tmpdir = tempfile.mkdtemp(prefix="jmdict_bridge_")
try:
    jmdict.close()
    jmdict.PACKS_DIR = tmpdir
    with open(os.path.join(tmpdir, jmdict.OLD_ZIP_NAME), "wb") as f:
        f.write(mini_zip(False))
    jmdict._download = lambda timeout: None          # the network is down
    assert jmdict.ensure_pack("ja"), "bridge build failed"
    assert jmdict.variant() == "plain", jmdict.variant()
    assert jmdict.lookup("戻る")[0].examples == ()
    # A later session with network upgrades the bridge in place…
    jmdict._download = lambda timeout: mini_zip(True)
    assert jmdict.ensure_pack("ja"), "bridge upgrade failed"
    assert jmdict.variant() == "examples", jmdict.variant()
    assert jmdict.lookup("戻る")[0].examples, "upgrade dropped the examples"
    # …and the spent old zip is cleaned up.
    assert not os.path.exists(os.path.join(tmpdir, jmdict.OLD_ZIP_NAME))
    print("PASS: an old zip bridges an offline upgrade, then upgrades itself")
finally:
    jmdict._download = orig_download
    jmdict.close()
    jmdict.PACKS_DIR = orig_dir
    shutil.rmtree(tmpdir, ignore_errors=True)

# ---- a word the pack has never heard of resolves to nothing ------------
# (Not fullwidth latin: JMdict really does list Ｚ, 'Z; z'.)
assert jmdict.word_at("", 0) is None
assert jmdict.resolve("", 0) is None
assert jmdict.word_at("戻る", 99) is None
print("PASS: unknown text resolves to None (the caller falls back)")

print("\nALL PASS")
