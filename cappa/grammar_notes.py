"""The Grammar tab's content layer: hand-written, popup-ready one-liners.

Pure data, no imports, no downloads, no Qt. (The Japanese notes live with
the deinflection rules they explain — language/japanese/jmdict.py — and
the Arabic Form I-X table with the classifier that emits the form numbers,
language/arabic/morphology.py.)

  ID_AFFIX_NOTES    the core Indonesian affixes as (affix, note) rows in
                    display order; indonesian.py names which of them a
                    surface form carries.

Voice: function first, then a tiny example with gloss. Accuracy over
coverage — a wrong grammar note teaches a wrong rule."""

# ---------------------------------------------------------------------------
# Indonesian: the core affixes as (affix, note) rows, display order.
# meN-/peN- nasal assimilation is noted where it matters.
# ---------------------------------------------------------------------------
ID_AFFIX_NOTES = (
    ("me-", "Active verb prefix (nasal shifts: mem-/men-/meng-/meny-): memakan 'to eat (something)' from makan."),
    ("di-", "Passive counterpart of me-: dimakan 'eaten (by someone)'."),
    ("ber-", "Intransitive 'have / use / do': berjalan 'to walk' (jalan 'way'), bersepeda 'to ride a bike'."),
    ("ter-", "Unintentional or resulting state: tertidur 'fell asleep (without meaning to)'; on adjectives, superlative: terbaik 'best'."),
    ("ke-...-an", "Abstract-state noun: kebersihan 'cleanliness' (bersih 'clean'); also 'adversely affected by': kehujanan 'caught in the rain'."),
    ("pe-...-an", "Noun for the process of the verb: pembangunan 'construction, development' (membangun 'to build')."),
    ("-kan", "Makes the verb transitive — causative or 'for someone': membersihkan 'to clean (make clean)', membacakan 'to read aloud for (someone)'."),
    ("-i", "Transitive suffix — action onto a place or object, often repeated: menulisi 'to write on (something)', mengunjungi 'to visit'."),
    ("-an", "Result or object noun: makanan 'food' (makan 'to eat'), minuman 'a drink' (minum 'to drink')."),
    ("-nya", "His/her/its — or marks the thing as known: bukunya 'his/her book; the book'."),
    ("X-X", "Reduplication — plurality, variety, or doing something casually: jalan-jalan 'go for a stroll', makan-makan 'have a meal together'."),
    ("se-", "'One / same / as ... as': serumah 'in the same house', sebesar 'as big as'."),
    ("memper-", "Causative 'make more X, treat as X': memperbesar 'to enlarge' (besar 'big')."),
)
