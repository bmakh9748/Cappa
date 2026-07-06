"""Unit test for cappa/translate.py — the word cleanup the popup shows, and
the context-marking round trip (mark the word in its sentence, pull the
marked span back out of the translation).

Pure string work, no network: translate() itself is a live Google call and
is exercised by clicking a word in the running app, not here."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cappa.translate import _extract_marked, _mark, clean_word

CASES = [
    ("hello,", "hello"),              # trailing comma from the line
    ('"Wait!"', "Wait"),              # quotes + bang
    ("(word)", "word"),               # brackets
    ("word...", "word"),              # ellipsis
    ("don't", "don't"),               # INNER punctuation survives
    ("「こんにちは」",
     "こんにちは"),   # 「こんにちは」-> こんにちは
    ("食べた。", "食べた"),  # 食べた。
    ("♪歌♪", "歌"),  # ♪歌♪ -> 歌 (symbols strip too)
    ("ハロー・ワールド",
     "ハロー・ワールド"),  # inner ・ stays
    ("word ,", "word"),               # stray space before the mark
    ("...", ""),                      # pure punctuation -> nothing left
    ("", ""),
    (None, ""),
]
for raw, want in CASES:
    got = clean_word(raw)
    assert got == want, "FAIL: clean_word(%r) = %r, wanted %r" % (raw, got, want)
print("PASS: %d cleanup cases (en, ja, symbols, edge-only)" % len(CASES))

MARK_CASES = [
    # the word is quoted in place, standalone occurrences only
    ("وغير معروفا", "معروفا", 'وغير "معروفا"'),
    ("I know that word", "know", 'I "know" that word'),
    ("Bawa itu", "bawa", '"Bawa" itu'),            # case-insensitive, keeps
                                                    # the sentence's casing
    ("the theory holds", "theory", 'the "theory" holds'),  # 'the' untouched
    ("vi ser dig, ses.", "ses", 'vi ser dig, "ses".'),     # not inside 'ser'
    ("食べたことがある", "食べた", '"食べた"ことがある'),   # CJK: plain find
    ("hello world", "missing", None),               # not in the sentence
    ("", "word", None),
    ("sentence", "", None),
]
for sent, word, want in MARK_CASES:
    got = _mark(sent, word)
    assert got == want, "FAIL: _mark(%r, %r) = %r, wanted %r" % (
        sent, word, got, want)
print("PASS: %d marking cases (ar, en, cjk, boundaries)" % len(MARK_CASES))

EXTRACT_CASES = [
    ('and not "known"', "معروفا", "known"),          # marks survived
    ("and not “known”", "معروفا", "known"),          # curly quotes
    ("et pas «connu»", "معروفا", "connu"),           # guillemets
    ("and unknown", "معروفا", ""),                   # marks dropped -> miss
    ('so "BAWA" it is', "bawa", ""),                 # left untranslated -> miss
    ('"a very long span of far too many words here" x', "w", ""),
    ("", "word", ""),
]
for text, word, want in EXTRACT_CASES:
    got = _extract_marked(text, word)
    assert got == want, "FAIL: _extract_marked(%r, %r) = %r, wanted %r" % (
        text, word, got, want)
print("PASS: %d extraction cases (survive, drop, untranslated)"
      % len(EXTRACT_CASES))

print("ALL PASS")
