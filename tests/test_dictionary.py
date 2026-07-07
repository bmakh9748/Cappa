"""Unit test: Wiktionary definition formatting, hint-based sense ordering,
and meaning()'s dictionary-first / translation-fallback contract. Network-free:
lookup() is monkeypatched with canned entries shaped like the real REST
responses (cards 0065/0066's cases)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cappa.dictionary as D
from cappa import translate
from cappa.translate import TranslationError


def main():
    # _clean_gloss: real Wiktionary definition HTML reduces to plain text.
    markup = ('<span class="usage-label-sense"></span> '
              '<a rel="mw:WikiLink" href="/wiki/that" title="that">that</a>')
    assert D._clean_gloss(markup) == "that", repr(D._clean_gloss(markup))
    assert D._clean_gloss("  a&amp;b\n c ") == "a&b c"
    print("PASS dictionary: glosses strip HTML and entities")

    # _format: the entry agreeing with the in-context hint is hoisted first
    # (LIAT: Google says "sees" -> the lihat verb sense outranks "rubbery"),
    # everything else keeps Wiktionary's order; long senses are trimmed.
    liat = [
        ("adjective", ["rubbery", "clayey (of soil)"]),
        ("noun", ["clay: a mineral substance made up of small crystals of "
                  "silica and alumina, that is ductile when moist"]),
        ("verb", ["alternative form of lihat (to see; to look; to stare)"]),
    ]
    text = D._format(liat, hint="sees")
    lines = text.split("\n")
    assert lines[0].startswith("verb: alternative form of lihat"), lines
    assert lines[1].startswith("adjective: rubbery"), lines
    assert lines[2].endswith("…") and len(lines[2]) < 110, lines[2]
    assert D._format(liat, hint="")[0:9] == "adjective"  # no hint: page order
    print("PASS dictionary: hint hoists the agreeing sense, trims long ones")

    # meaning(): dictionary when entries exist, translation only as hint.
    orig_lookup = D.lookup
    orig_source, orig_target = translate.SOURCE_LANGUAGE, translate.TARGET_LANGUAGE
    calls = []

    def fake_translate(word, sentence=""):
        calls.append(word)
        return "sees"

    try:
        translate.SOURCE_LANGUAGE, translate.TARGET_LANGUAGE = "id", "en"
        D._cache.clear()
        D.lookup = lambda word, lang: [("conjunction", ["that", "which"]),
                                       ("pronoun", ["one"])]
        got = D.meaning("YANG", "GAK ADA YANG LIAT!",
                        translate_fn=fake_translate)
        assert got == "conjunction: that; which\npronoun: one", got
        assert calls == ["YANG"], calls   # translation fetched (as the hint)
        print("PASS dictionary: definitions replace the contextual guess")

        # No entry (ngupil): the contextual translation passes through.
        D._cache.clear()
        D.lookup = lambda word, lang: []
        assert D.meaning("Ngupil", "Milyhya Lagi Ngupil!",
                         translate_fn=lambda w, s="": "picking her nose"
                         ) == "picking her nose"
        # Network trouble in the dictionary (None): same fallback.
        D._cache.clear()
        D.lookup = lambda word, lang: None
        assert D.meaning("kata", translate_fn=lambda w, s="": "word") == "word"
        print("PASS dictionary: missing entry falls back to translation")

        # Both sources empty -> the translation error is what surfaces.
        D._cache.clear()
        D.lookup = lambda word, lang: []

        def broken(word, sentence=""):
            raise TranslationError("no connection")

        try:
            D.meaning("kata", translate_fn=broken)
            raise AssertionError("expected TranslationError")
        except TranslationError as exc:
            assert "no connection" in str(exc)
        print("PASS dictionary: offline still raises a displayable error")

        # auto source / non-English target: pure pass-through, no lookup.
        def exploding_lookup(word, lang):
            raise AssertionError("lookup must not run")

        D.lookup = exploding_lookup
        translate.SOURCE_LANGUAGE = "auto"
        assert D.meaning("BAWA", translate_fn=lambda w, s="": "bring") == "bring"
        translate.SOURCE_LANGUAGE, translate.TARGET_LANGUAGE = "id", "ar"
        assert D.meaning("BAWA", translate_fn=lambda w, s="": "x") == "x"
        print("PASS dictionary: auto source / non-en target bypass lookup")

        # A bare "form of X" gloss pulls X's own meaning along: card_0073's
        # dipikir said "passive of pikir" and taught nothing. A form-of
        # gloss that ALREADY carries a parenthetical meaning is left alone.
        translate.SOURCE_LANGUAGE, translate.TARGET_LANGUAGE = "id", "en"
        D._cache.clear()
        canned = {"dipikir": [("verb", ["passive of pikir"])],
                  "pikir": [("verb", ["to think; to consider"])],
                  "liat": [("verb", ["alternative form of lihat (to see)"])]}
        D.lookup = lambda word, lang: canned.get(word.lower(), [])
        got = D.meaning("dipikir", "Istrinya juga dipikir, mas!",
                        translate_fn=lambda w, s="": "thought of")
        assert got == "verb: passive of pikir (to think; to consider)", got
        got = D.meaning("liat", translate_fn=lambda w, s="": "see")
        assert got == "verb: alternative form of lihat (to see)", got
        print("PASS dictionary: form-of glosses carry the base meaning")

        # The cache short-circuits repeat clicks.
        D._cache.clear()
        D.lookup = lambda word, lang: [("noun", ["word"])]
        first = D.meaning("kata", translate_fn=lambda w, s="": "word")
        D.lookup = exploding_lookup
        assert D.meaning("kata", translate_fn=broken) == first
        print("PASS dictionary: cached meaning skips the network")
    finally:
        D.lookup = orig_lookup
        translate.SOURCE_LANGUAGE, translate.TARGET_LANGUAGE = (orig_source,
                                                                orig_target)
        D._cache.clear()
    print("ALL PASS")


if __name__ == "__main__":
    main()
