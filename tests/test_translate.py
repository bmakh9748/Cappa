"""Unit test for cappa/translate.py — the word cleanup the popup shows.

Pure string work, no network: translate() itself is a live Google call and
is exercised by clicking a word in the running app, not here."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cappa.translate import clean_word

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

print("ALL PASS")
