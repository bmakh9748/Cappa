"""Unit test: the Grammar tab's content tables stay complete and honest.
(The Japanese tripwire lives in test_jmdict.py, beside the rules it pins.)
Pure data, no network, no Qt."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cappa import grammar_notes, indonesian


def main():
    # ---- Arabic: the ten forms, I through X ------------------------------
    names = [row[0] for row in grammar_notes.AR_VERB_FORMS]
    assert names == ["I", "II", "III", "IV", "V",
                     "VI", "VII", "VIII", "IX", "X"], names
    for _name, pattern, translit, note in grammar_notes.AR_VERB_FORMS:
        assert pattern and translit and note
    got = grammar_notes.arabic_form_note("X")
    assert got is not None and got[1] == "istafʿala", got
    assert grammar_notes.arabic_form_note("XI") is None
    print("PASS grammar_notes: the Form I-X table is complete and ordered")

    # ---- Indonesian: every label the affix reader can emit is covered ----
    notes = dict(grammar_notes.ID_AFFIX_NOTES)
    assert len(notes) == len(grammar_notes.ID_AFFIX_NOTES), "duplicate affix"
    emittable = ({label for _p, label in indonesian._PREFIXES}
                 | {label for _s, label in indonesian._SUFFIXES}
                 | {"ke-...-an", "pe-...-an", "X-X"}
                 | {label for _r, labels in indonesian._OVERRIDES.values()
                    for label in labels})
    missing = emittable - set(notes)
    assert not missing, "affix labels without a note: %s" % sorted(missing)
    print("PASS grammar_notes: every emittable affix label has its note")

    print("ALL PASS")


if __name__ == "__main__":
    main()
