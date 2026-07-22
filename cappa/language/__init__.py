"""The word-data layer: everything that answers questions about words.

Shared machinery at this level (works for any language), one folder per
studied language below it. The map:

    translate.py   sentence/word translation (deep-translator's free Google
                   endpoint — never an LLM)
    dictionary.py  word meanings: Wiktionary first, Google hint/fallback
    examples.py    example sentences: JMdict pack (ja, offline), Wiktionary,
                   Tatoeba top-up
    pronounce.py   word audio: free Google TTS fetch + winmm playback
    lexicon.py     per-language word-frequency packs: splits glued OCR runs
    grammar.py     the word's anatomy as rich text (inflection chain +
                   kanji / root+form / affixes) — the popup's Grammar tab
                   and the card's Breakdown field share it

Every module and subpackage here is Qt-free and blocking — the UI calls
them from helper threads. Free, key-less endpoints only (the house rule)."""
