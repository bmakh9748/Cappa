"""Indonesian word anatomy: the root under the affixes.

The Grammar tab's Indonesian half. Indonesian builds words by stacking
affixes on a root (me- + makan, ke- + hilang + -an), and Wiktionary only
names the base for di-/me- voice forms — lexicalized derivations
(makanan, berjalan, pembelajaran) arrive as bare glosses. Sastrawi (the
Python port of the Nazief-Adriani stemmer, MIT, pure Python, ~28 ms a
word) recovers the root; the affixes are then read off the surface by
diffing it against that root, and each identified affix maps to its
grammar_notes.ID_AFFIX_NOTES one-liner.

    anatomy("memakan")  -> ("makan", ("me-",))
    anatomy("kebersihan") -> ("bersih", ("ke-...-an",))
    anatomy("makan")    -> None (its own root: nothing to explain)

meN-/peN- nasal assimilation swallows the root's first letter (menulis =
men + (t)ulis); _split regrows it before giving up. An affix the labeler
doesn't recognize fails soft: the root is still returned with no labels,
and no-stemmer/no-match returns None outright.

Sastrawi's bundled root list derives from Kateglo (CC BY-NC-SA) — fine
for this personal app, noted in the README. Deferred/lazy import so the
app and suite run without the package. Blocking but CPU-only and fast;
the popup calls it on its grammar thread. No Qt."""

import threading

LANG = "id"

_lock = threading.Lock()
_stemmer = None
_failed = False    # Sastrawi missing: remembered, not retried per click


# Surface prefix chunk -> the affix row it belongs to in ID_AFFIX_NOTES.
# Longest first so memper- wins over me-, meng- over me-. The peN-/ke-
# rows label as circumfixes only when the -an tail is present (handled in
# anatomy()); a bare pe-/ke- prefix stays unlabeled rather than mislabeled.
_PREFIXES = (
    ("memper", "memper-"),
    ("meng", "me-"), ("meny", "me-"), ("mem", "me-"), ("men", "me-"),
    ("me", "me-"),
    ("ber", "ber-"), ("ter", "ter-"), ("di", "di-"), ("se", "se-"),
)
_CIRCUMFIX_HEADS = ("peng", "peny", "pem", "pen", "per", "pe", "ke")
# Suffixes peeled from the END, longest first (-kan before -an; bukunya,
# dimakannya stack -nya after others).
_SUFFIXES = (("kan", "-kan"), ("nya", "-nya"), ("an", "-an"), ("i", "-i"))

# meN-/peN- assimilation: the nasal replaces the root's first letter.
_NASAL_HEADS = ("meny", "men", "mem", "meng", "peny", "pen", "pem", "peng")

# Sastrawi picks a wrong-but-plausible segmentation for a few very common
# words (berikan is beri + -kan 'give!', not ber- + ikan 'to have fish';
# berpegangan is ber- + pegang + -an 'hold on', not ber- + gang). The
# popup must never teach the fish reading — verified traps are pinned.
_OVERRIDES = {
    "berikan": ("beri", ("-kan",)),
    "berpegangan": ("pegang", ("ber-", "-an")),
}

# di + place, spelled fused (informal subtitles love dimana/disana): the
# di- is the preposition 'in/at', not the passive prefix — better silent
# than teaching the wrong rule.
_DI_PLACES = frozenset(("mana", "sana", "sini", "situ", "rumah", "atas",
                        "bawah", "dalam", "luar", "depan", "belakang"))


def _load():
    """The Sastrawi stemmer, constructed once; None when uninstalled."""
    global _stemmer, _failed
    with _lock:
        if _stemmer is not None or _failed:
            return _stemmer
        try:
            from Sastrawi.Stemmer.StemmerFactory import StemmerFactory
            _stemmer = StemmerFactory().create_stemmer()
        except Exception as exc:
            print("[cappa] indonesian: Sastrawi unavailable: %s" % exc)
            _failed = True
            return None
        return _stemmer


def status():
    """"" when the stemmer can answer; else a short displayable reason
    for the popup (fail soft, but say so). Meaningful after an anatomy()
    attempt, which is when _failed is settled."""
    return "Sastrawi not installed (see requirements.txt)" if _failed else ""


def root(word):
    """The Sastrawi root of a word (lowercased), or None (no stemmer, or
    nothing was stripped — the word is its own root)."""
    stemmer = _load()
    if stemmer is None or not word:
        return None
    w = word.lower()
    try:
        stem = stemmer.stem(w)
    except Exception:
        return None
    if not stem or stem == w or len(stem) < 2:
        return None
    return stem


def _split(word, stem):
    """(prefix chunk, suffix chunk) around the root's occurrence in the
    surface, regrowing a nasal-swallowed first letter (menulis = men +
    (t)ulis); (None, None) when the root cannot be located."""
    i = word.find(stem)
    if i >= 0:
        return word[:i], word[i + len(stem):]
    for head in _NASAL_HEADS:
        if (word.startswith(head) and len(stem) > 1
                and word[len(head):].startswith(stem[1:])):
            return word[:len(head)], word[len(head) + len(stem) - 1:]
    return None, None


def _peel_suffixes(suf):
    """Recognized suffixes peeled from the END of the leftover chunk:
    (labels, unrecognized remainder)."""
    labels = []
    while suf:
        for tail, label in _SUFFIXES:
            if suf.endswith(tail):
                labels.append(label)
                suf = suf[:-len(tail)]
                break
        else:
            break   # an unrecognized remainder: keep what was identified
    return labels, suf


def _reduplication(w):
    """makan-makan, berlari-lari, sayur-sayuran — the doubled word.
    (root, labels + 'X-X') when the halves really are a doubling; None for
    any other hyphenated thing (don't guess about compounds)."""
    left, _, right = w.partition("-")
    if not left or not right or "-" in right:
        return None
    if right != left and not left.endswith(right) \
            and not right.startswith(left):
        return None
    inner = anatomy(left)
    if inner is not None:
        stem, labels = inner
    else:
        stem = root(left) or left
        labels = ()
    extra = right[len(left):] if right.startswith(left) else ""
    peeled, _rest = _peel_suffixes(extra)
    return stem, labels + ("X-X",) + tuple(peeled)


def anatomy(word):
    """(root, affix labels) for an affixed word, or None when there is
    nothing to teach (no stemmer, unaffixed, root not locatable, or a
    known trap where a label would lie). Labels are ID_AFFIX_NOTES keys:
    circumfix/prefix first, inner suffixes next, the -nya enclitic last."""
    w = (word or "").lower()
    hit = _OVERRIDES.get(w)
    if hit:
        return hit
    if "-" in w:
        return _reduplication(w)
    stem = root(w)
    if stem is None:
        return None
    pre, suf = _split(w, stem)
    if pre is None:
        return stem, ()
    if pre == "di" and stem in _DI_PLACES:
        return None   # dimana: the preposition, not the passive

    labels = []
    # The -nya enclitic rides OUTSIDE everything (kesalahannya = ke- +
    # salah + -an + -nya): peel it before the circumfix looks at the tail.
    trailing = []
    if suf.endswith("nya"):
        trailing.append("-nya")
        suf = suf[:-3]
    # ke-...-an / pe(N)-...-an are circumfixes: both halves spend together.
    # startswith, not equality: pembelajaran's prefix chunk is 'pembel'
    # (peN- wrapped around the already-derived belajar) and the outer
    # circumfix is still the right top-level label. NOT when the tail's
    # 'an' really belongs to -kan: pertahankan is per- + tahan + -kan, an
    # imperative, no process noun anywhere near it.
    if (suf.endswith("an") and not suf.endswith("kan")
            and any(pre.startswith(h) for h in _CIRCUMFIX_HEADS)):
        labels.append("ke-...-an" if pre.startswith("ke") else "pe-...-an")
        pre, suf = "", suf[:-2]
    if pre:
        for head, label in _PREFIXES:
            if pre == head:
                labels.append(label)
                break
    peeled, _rest = _peel_suffixes(suf)
    return stem, tuple(labels + peeled + trailing)
