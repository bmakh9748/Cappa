"""Unit test: Arabic morphology (cappa.language.arabic).

verb_form()/_skeleton() are pure and always run. analyze() needs BOTH the
slim camel-tools install and the 40 MB morphology pack; without either the
analyzer half SKIPS (the module itself fails soft the same way). The
analyzer half loads ~400 MB for the duration of this test."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cappa.language.arabic import morphology as arabic

# ---- the letter skeleton: harakat out, shadda doubled, wasla folded ------
assert arabic._skeleton("كَتَبَ") == "كتب"
assert arabic._skeleton("تَكَلَّمَ") == "تكللم"      # shadda -> doubled ل
assert arabic._skeleton("ٱِسْتَغْفَر") == "استغفر"  # wasla -> plain alif
print("PASS arabic: the skeleton strips vowels but keeps the shadda's letter")

# ---- Form I-X classification on the classical patterns ------------------
CASES = [
    ("كَتَبَ", "I"), ("فَهِمَ", "I"),
    ("عَلَّمَ", "II"), ("كَاتَبَ", "III"), ("شَاهَدَ", "III"),
    ("أَخْرَجَ", "IV"), ("تَعَلَّمَ", "V"), ("تَكَاتَبَ", "VI"),
    ("اِنْكَسَرَ", "VII"), ("ٱِنْكَسَر", "VII"),
    ("اِجْتَمَعَ", "VIII"), ("اِحْمَرَّ", "IX"),
    ("اِسْتَغْفَرَ", "X"), ("ٱِسْتَطاع", "X"),
    # The check-order regressions (review 2026-07-21): a nun-initial
    # Form VIII is NOT the VII its ان opening suggests…
    ("اِنْتَظَر", "VIII"), ("اِنْتَقَل", "VIII"), ("اِتَّخَذ", "VIII"),
    # …a hamza-initial Form II is NOT the IV its أ opening suggests…
    ("أَكَّد", "II"), ("أَثَّر", "II"),
    # …and آ (madda) is the fused ʾā- of a hamza-initial Form IV.
    ("آمَنَ", "IV"),
]
for lemma, want in CASES:
    got = arabic.verb_form(lemma)
    assert got == want, (lemma, got, want)
# Shapes outside the ten forms answer None, not a guess.
assert arabic.verb_form("تَرْجَمَ") is None       # quadriliteral
assert arabic.verb_form("") is None
# The root breaks the IX-shape tie: a true IX keeps its label, the
# assimilated-ت Form VIII geminate (اضطر, root ض ر ر) refuses the wrong
# one instead of claiming a color-verb pattern.
assert arabic.verb_form("اِحْمَرَّ", "ح.م.ر") == "IX"
assert arabic.verb_form("اِضْطَرّ", "ض.ر.ر") is None
print("PASS arabic: %d lemmas classify to their classical form" % len(CASES))

# ---- the Form I-X table: complete, ordered, and honest ------------------
names = [row[0] for row in arabic.VERB_FORMS]
assert names == ["I", "II", "III", "IV", "V",
                 "VI", "VII", "VIII", "IX", "X"], names
for _name, pattern, translit, note in arabic.VERB_FORMS:
    assert pattern and translit and note
got = arabic.form_note("X")
assert got is not None and got[1] == "istafʿala", got
assert arabic.form_note("XI") is None
print("PASS arabic: the Form I-X table is complete and ordered")

# ---- gating: only "ar" ever downloads anything --------------------------
assert arabic.ensure_pack("ja") is False
assert arabic.ensure_pack(None) is False
print("PASS arabic: non-Arabic languages never fetch the pack")

if not arabic.ready():
    print("SKIP: no morphology pack (offline?) — analyzer assertions "
          "skipped")
    sys.exit(0)
try:
    import camel_tools  # noqa: F401  (the slim install)
except ImportError:
    print("SKIP: camel-tools not installed — analyzer assertions skipped")
    sys.exit(0)

# ---- the analyzer: the probe's verified samples -------------------------
a = arabic.analyze("استغفر")
assert a is not None
assert a.root == "غ ف ر" and a.form == "X" and a.pos == "verb", (
    a.root, a.form, a.pos)
assert "forgiveness" in a.gloss, a.gloss

# Clitics strip: و + ال + كتاب still finds the bare noun.
a = arabic.analyze("والكتاب")
assert a is not None and a.root == "ك ت ب" and a.pos == "noun", (
    a.root, a.pos)
assert a.form is None       # nouns carry no verb form

# A loanword says so instead of faking a root.
a = arabic.analyze("تلفزيون")
assert a is not None and a.loan and a.root == "", (a.loan, a.root)
assert "television" in a.gloss

# End to end: the everyday Form VIII verbs come back as VIII (the review's
# live repro — انتظر was labeled VII before the check-order fix).
a = arabic.analyze("انتظر")
assert a is not None and a.form == "VIII", (a.form, a.root)
a = arabic.analyze("يؤكد")
assert a is not None and a.form == "II", (a.form, a.lemma)

# Empty input resolves to None (backoff stays off — no guess-noise).
assert arabic.analyze("") is None
print("PASS arabic: analyses carry root, form, pos and an honest loanword "
      "flag")

print("\nALL PASS")
