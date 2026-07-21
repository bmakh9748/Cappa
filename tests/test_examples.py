"""Unit test: example-sentence gathering (cappa.examples). Network-free:
the Tatoeba GET and the Wiktionary reader are monkeypatched with canned
payloads shaped like the real responses (probed 2026-07-20); the pack leg
runs _from_pack against a hand-built jmdict.Entry, so no pack is needed
either."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cappa.examples as E
from cappa import dictionary, jmdict, translate


def entry_with(examples):
    return jmdict.Entry("戻る", "もどる", [(("v5r",), ("to return",))], 1,
                        tuple(examples))


# The new-API shape: data rows with a flat translations list (is_direct
# marks a directly linked pair; a pivot translation must lose to it).
TATOEBA = {
    "data": [
        {"text": "Ayo makan!", "lang": "ind", "translations": [
            {"text": "Let's eat!", "lang": "eng", "is_direct": True}]},
        {"text": "Sebelum makan?", "lang": "ind", "translations": [
            {"text": "By way of France.", "lang": "eng", "is_direct": False},
            {"text": "Before eating?", "lang": "eng", "is_direct": True}]},
        # No English translation at all: must be skipped.
        {"text": "Makan tanpa terjemahan.", "lang": "ind", "translations": [
            {"text": "sans traduction", "lang": "fra", "is_direct": True}]},
        # Longer than MAX_CHARS: must be skipped.
        {"text": "makan " * 20, "lang": "ind", "translations": [
            {"text": "long", "lang": "eng", "is_direct": True}]},
        {"text": "Dia sedang makan.", "lang": "ind", "translations": [
            {"text": "He is eating.", "lang": "eng", "is_direct": True}]},
        # Unapproved (unvetted) sentence: must be skipped.
        {"text": "Jangan makan!", "lang": "ind", "is_unapproved": True,
         "translations": [
            {"text": "Don't eat!", "lang": "eng", "is_direct": True}]},
    ],
    "paging": {"total": 6},
}


def main():
    # ---- _from_pack: a real Entry's triples become Examples --------------
    orig_jmdict_lookup = jmdict.lookup
    try:
        jmdict.lookup = lambda form: [entry_with([
            ("戻ってきた。", "He came back.", "戻って"),
            ("こ" * 120, "a paragraph, not a popup line", "戻る"),
        ])]
        got = E._from_pack("戻る")
        assert len(got) == 1, got            # the paragraph was filtered
        assert got[0].text == "戻ってきた。" and got[0].form == "戻って"
        assert got[0].source == "Tatoeba"
        jmdict.lookup = lambda form: []
        assert E._from_pack("ない") == []
    finally:
        jmdict.lookup = orig_jmdict_lookup
    print("PASS examples: pack triples become Examples; paragraphs filtered")

    orig_get = E._get_json
    orig_wikt = dictionary.examples
    orig_pack = E._from_pack
    orig_source = translate.SOURCE_LANGUAGE
    orig_target = translate.TARGET_LANGUAGE
    urls = []

    def fake_get(url):
        urls.append(url)
        return TATOEBA

    def no_get(url):
        raise AssertionError("network GET must not run here")

    try:
        # ---- Indonesian: Wiktionary first, Tatoeba tops up to LIMIT ----
        translate.SOURCE_LANGUAGE, translate.TARGET_LANGUAGE = "id", "en"
        E._cache.clear()
        dictionary.examples = lambda w, lang: [
            ("Pagi ini saya makan ikan.", "This morning I ate fish.", "")]
        E._get_json = fake_get
        got = E.sentences("makan")
        assert len(got) == E.LIMIT, got
        assert got[0].text == "Pagi ini saya makan ikan."
        assert got[0].source == "Wiktionary" and got[0].translation
        assert got[1].text == "Ayo makan!" and got[1].source == "Tatoeba"
        # The direct translation wins over the pivot listed before it.
        assert got[2].translation == "Before eating?", got[2].translation
        # Exact-form query, ISO 639-3 codes, target filter — all in the URL.
        assert len(urls) == 1
        assert "lang=ind" in urls[0] and "%3Dmakan" in urls[0], urls[0]
        assert "trans%3Alang=eng" in urls[0], urls[0]
        print("PASS examples: Wiktionary leads, Tatoeba tops up, direct "
              "translations win")

        # ---- unvetted and untranslated rows never reach a learner -------
        rows = E._from_tatoeba("makan", "id", "en")
        texts = [e.text for e in rows]
        assert "Jangan makan!" not in texts, texts       # is_unapproved
        assert "Makan tanpa terjemahan." not in texts    # no eng at all
        print("PASS examples: unapproved and untranslated rows are skipped")

        # ---- the cache makes the second call free -----------------------
        dictionary.examples = no_get
        E._get_json = no_get
        again = E.sentences("makan")
        assert [e.text for e in again] == [e.text for e in got]
        print("PASS examples: repeat lookups come from the cache")

        # ---- every source failing is an error, not a silent blank -------
        E._cache.clear()
        dictionary.examples = lambda w, lang: None      # network trouble

        def get_dies(url):
            raise OSError("down")

        E._get_json = get_dies
        try:
            E.sentences("makan")
            raise AssertionError("expected ExamplesError")
        except E.ExamplesError as exc:
            assert "internet" in str(exc)
        print("PASS examples: total network failure raises a displayable "
              "reason")

        # ---- a partial answer while a source is down is NOT cached ------
        E._cache.clear()
        dictionary.examples = lambda w, lang: [("Satu contoh.", "One.", "")]
        E._get_json = get_dies                  # Tatoeba down
        got = E.sentences("contoh")
        assert len(got) == 1, got               # what was salvageable shows
        E._get_json = fake_get                  # …and the next click retries
        got = E.sentences("contoh")
        assert len(got) == E.LIMIT, got
        print("PASS examples: a partial answer retries; only full results "
              "cache")

        # ---- sources legitimately empty -> just [] ----------------------
        E._cache.clear()
        dictionary.examples = lambda w, lang: []
        E._get_json = lambda url: {"data": []}
        assert E.sentences("tidur") == []
        print("PASS examples: no examples anywhere is an empty list, no "
              "error")

        # ---- auto source: nothing to ask anyone -------------------------
        E._cache.clear()
        translate.SOURCE_LANGUAGE = "auto"
        dictionary.examples = no_get
        E._get_json = no_get
        assert E.sentences("word") == []
        print("PASS examples: auto-detect skips every web source")

        # ---- non-English target: Wiktionary (English) is skipped --------
        E._cache.clear()
        translate.SOURCE_LANGUAGE, translate.TARGET_LANGUAGE = "id", "ar"
        dictionary.examples = no_get
        urls.clear()
        E._get_json = lambda url: (urls.append(url) or {"data": []})
        E.sentences("makan")
        assert len(urls) == 1 and "trans%3Alang=ara" in urls[0], urls
        print("PASS examples: non-en target skips Wiktionary, retargets "
              "Tatoeba")

        # ---- Japanese: the pack answers offline -------------------------
        translate.SOURCE_LANGUAGE, translate.TARGET_LANGUAGE = "ja", "en"
        E._cache.clear()
        pack = [("戻ってきた。", "He came back.", "戻って"),
                ("すぐ戻る。", "I'll be right back.", "戻る"),
                ("家に戻る。", "I return home.", "戻る")]
        E._from_pack = lambda form: [
            E.Example(jp, en, form=used, source="Tatoeba")
            for jp, en, used in pack]
        dictionary.examples = no_get
        E._get_json = no_get          # LIMIT pack pairs -> no top-up call
        got = E.sentences("戻って", lemma="戻る")
        assert len(got) == E.LIMIT and got[0].text == "戻ってきた。"
        assert got[0].form == "戻って"   # the surface the sentence contains
        print("PASS examples: Japanese answers from the pack, offline")

        # ---- a thin pack entry still tops up from Tatoeba ---------------
        E._cache.clear()
        E._from_pack = lambda form: [
            E.Example("すぐ戻る。", "I'll be right back.", form="戻る",
                      source="Tatoeba")]
        called = []
        E._get_json = lambda url: (called.append(url) or {"data": []})
        got = E.sentences("戻る")
        assert len(got) == 1 and called, (got, called)
        print("PASS examples: a thin pack entry still asks Tatoeba")

        # ---- duplicates collapse ----------------------------------------
        items = [E.Example("Ayo makan!", "a"), E.Example("AYO MAKAN!", "b"),
                 E.Example("Lain.", "c")]
        assert [e.text for e in E._dedupe(items)] == ["Ayo makan!", "Lain."]
        print("PASS examples: dedupe folds case-identical sentences")
    finally:
        E._get_json = orig_get
        E._from_pack = orig_pack
        dictionary.examples = orig_wikt
        translate.SOURCE_LANGUAGE = orig_source
        translate.TARGET_LANGUAGE = orig_target
        E._cache.clear()
    print("ALL PASS")


if __name__ == "__main__":
    main()
