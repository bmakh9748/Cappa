"""Unit test: Indonesian word anatomy (cappa.indonesian).

_split() and the affix labeler are pure and always run (fed a known root
directly). The Sastrawi-backed half SKIPS when the package is absent —
the module itself fails soft the same way."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cappa.language.indonesian import affixes as indonesian

# ---- _split: locating the root inside the surface -----------------------
assert indonesian._split("memakan", "makan") == ("me", "")
assert indonesian._split("makanan", "makan") == ("", "an")
assert indonesian._split("dimakannya", "makan") == ("di", "nya")
# meN- swallowed the root's first letter; _split regrows it.
assert indonesian._split("menulis", "tulis") == ("men", "")
assert indonesian._split("memilih", "pilih") == ("mem", "")
# A root that simply isn't in the surface gives up cleanly.
assert indonesian._split("xyz", "abc") == (None, None)
print("PASS indonesian: _split finds the root, nasal assimilation included")

# ---- Sastrawi-backed anatomy --------------------------------------------
try:
    import Sastrawi  # noqa: F401
except ImportError:
    print("SKIP: Sastrawi not installed — anatomy assertions skipped")
    sys.exit(0)

CASES = [
    ("memakan", "makan", ("me-",)),
    ("dimakan", "makan", ("di-",)),
    ("makanan", "makan", ("-an",)),
    ("berjalan", "jalan", ("ber-",)),
    ("terjatuh", "jatuh", ("ter-",)),
    ("kebersihan", "bersih", ("ke-...-an",)),
    ("pembangunan", "bangun", ("pe-...-an",)),
    ("pembelajaran", "ajar", ("pe-...-an",)),
    ("membacakan", "baca", ("me-", "-kan")),
    ("bukunya", "buku", ("-nya",)),
    ("memperbesar", "besar", ("memper-",)),
    ("sebenarnya", "benar", ("se-", "-nya")),
    # The review's mislabel repros (2026-07-21):
    # -kan imperatives are NOT process-noun circumfixes…
    ("pertahankan", "tahan", ("-kan",)),
    ("kesampingkan", "samping", ("-kan",)),
    # …-nya stacks OUTSIDE a circumfix without hiding it…
    ("kesalahannya", "salah", ("ke-...-an", "-nya")),
    # …reduplications are doublings, not stray suffixes…
    ("makan-makan", "makan", ("X-X",)),
    ("jalan-jalan", "jalan", ("X-X",)),
    ("berlari-lari", "lari", ("ber-", "X-X")),
    ("sayur-sayuran", "sayur", ("X-X", "-an")),
    # …and Sastrawi's fish trap is pinned shut.
    ("berikan", "beri", ("-kan",)),
]
for word, want_root, want_labels in CASES:
    got = indonesian.anatomy(word)
    assert got is not None, word
    root, labels = got
    assert root == want_root, (word, root, want_root)
    assert labels == want_labels, (word, labels, want_labels)
print("PASS indonesian: %d affixed words name their root and affixes"
      % len(CASES))

# A word that IS its own root teaches nothing — and says so with None.
for word in ("makan", "jalan", "dia", ""):
    assert indonesian.anatomy(word) is None, word
# Fused di+place is the preposition, not the passive — silence over a lie.
for word in ("dimana", "disana", "dirumah"):
    assert indonesian.anatomy(word) is None, word
print("PASS indonesian: unaffixed words and di+place fusions stay quiet")

# AFFIX_NOTES covers every label the affix reader can emit — the tripwire
# that a new label cannot ship noteless (and no duplicate rows).
notes = dict(indonesian.AFFIX_NOTES)
assert len(notes) == len(indonesian.AFFIX_NOTES), "duplicate affix"
emittable = ({label for _p, label in indonesian._PREFIXES}
             | {label for _s, label in indonesian._SUFFIXES}
             | {"ke-...-an", "pe-...-an", "X-X"}
             | {label for _r, labels in indonesian._OVERRIDES.values()
                for label in labels})
missing = emittable - set(notes)
assert not missing, "affix labels without a note: %s" % sorted(missing)
print("PASS indonesian: every emittable affix label has its note")

print("\nALL PASS")
