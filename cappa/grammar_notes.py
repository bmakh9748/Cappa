"""The Grammar tab's content layer: hand-written, popup-ready one-liners.

Pure data, no imports, no downloads, no Qt. Three tables, one per language
of the reduced roster:

  JA_GRAMMAR_NOTES  one note per deinflection 'reason' jmdict._RULES can
                    emit — a Match's reasons chain maps each step through
                    this table (test_grammar_notes proves the key sets stay
                    equal). Unknown keys fail soft to the raw reason string.
  AR_VERB_FORMS     the ten Arabic verb forms (awzān) on the root ف-ع-ل:
                    (form, pattern, transliteration, note). arabic.py's
                    verb_form() answers "X"; this table explains what X is.
  ID_AFFIX_NOTES    the core Indonesian affixes as (affix, note) rows in
                    display order; indonesian.py names which of them a
                    surface form carries.

Voice: function first, then a tiny example with gloss. Accuracy over
coverage — a wrong grammar note teaches a wrong rule."""

# ---------------------------------------------------------------------------
# Japanese: one note per deinflection 'reason' cappa/jmdict.py _RULES emits.
# Keys are the EXACT reason strings (28 of them).
# ---------------------------------------------------------------------------
JA_GRAMMAR_NOTES = {
    "-te iru": "Ongoing action or resulting state: 食べている 'is eating', 知っている 'knows'.",
    "-te iru (polite)": "Polite -te imasu — ongoing action or resulting state: 食べています 'is eating'.",
    "-te iru (contraction)": "Casual speech drops the い of -te iru: 食べてる 'is eating'.",
    "-te oku": "Do in advance, leave it done for later: 買っておく 'buy (ready for later)'.",
    "-te oku (contraction)": "Casual -toku, squeezed from -te oku: 買っとく '(I'll) buy it (for later)'.",
    "-te aru": "State left by a deliberate action: 書いてある '(it) is written (someone wrote it)'.",
    "-te shimau": "Completely done, often with regret — 'end up doing': 忘れてしまう 'end up forgetting'.",
    "-te shimau (contraction)": "Casual -chau/-jau, squeezed from -te shimau: 食べちゃう 'end up eating'.",
    "-te iku": "Change or motion away from here/now — 'go on doing': 増えていく 'will keep increasing'.",
    "-te kuru": "Change or motion toward here/now — 'come to, has been doing': 見えてくる 'comes into view'.",
    "-te kureru": "Someone kindly does it for me/us: 教えてくれる '(they) tell me (as a favor)'.",
    "-te morau": "Get someone to do it — the favor received: 教えてもらう 'have (them) tell me'.",
    "-te miru": "Try doing and see: 食べてみる 'try eating'.",
    "-te": "Te-form — links to the next clause or an auxiliary; alone it is a casual request: 待って 'wait'.",
    "past": "Plain past (-ta; adjectives -katta): 食べた 'ate', 高かった 'was expensive'.",
    "polite": "Polite -masu form, neutral politeness: 食べます 'eat(s)'.",
    "negative": "Plain negative (-nai; adjectives -ku nai): 食べない 'doesn't eat', 高くない 'not expensive'.",
    "past negative": "Plain past negative -nakatta: 食べなかった 'didn't eat'.",
    "potential": "Can, be able to: 読める 'can read' (from 読む 'read').",
    "potential/passive": "-rareru — 'can do' or 'is done to'; context decides: 食べられる 'can eat / is eaten'.",
    "passive": "Passive — the subject is acted on: 招待される 'is invited'.",
    "causative": "Causative — make or let someone do: 勉強させる 'make (someone) study'.",
    "conditional": "-eba conditional — 'if/when': 食べれば 'if (you) eat', 高ければ 'if it's expensive'.",
    "volitional": "Volitional — 'let's / I'll': 行こう 'let's go', 食べよう 'let's eat'.",
    "imperative": "Plain command, blunt: 待て 'wait!', 食べろ 'eat!'.",
    "-tai": "Want to do: 食べたい 'want to eat'.",
    "adverbial": "Adjective -ku form used as an adverb: 早く 'quickly' (from 早い 'fast').",
    "noun form": "Adjective -sa form — the quality as a measurable noun: 高さ 'height' (from 高い 'high').",
}

# ---------------------------------------------------------------------------
# Arabic: the ten verb forms (awzān) on the root ف-ع-ل. Rows are
# (form, pattern, transliteration, note). ʿ = ayn, ʾ = hamza.
# ---------------------------------------------------------------------------
AR_VERB_FORMS = (
    ("I", "فَعَلَ", "faʿala",
     "The base verb — the root's plain meaning (middle vowel varies): كَتَبَ kataba 'he wrote'."),
    ("II", "فَعَّلَ", "faʿʿala",
     "Doubled middle root letter — causative or intensive of I: عَلَّمَ ʿallama 'he taught' (I عَلِمَ ʿalima 'he knew')."),
    ("III", "فَاعَلَ", "fāʿala",
     "Long ā after the first root letter — action directed at someone: كَاتَبَ kātaba 'he corresponded with'."),
    ("IV", "أَفْعَلَ", "ʾafʿala",
     "Prefix ʾa- — causative: أَخْرَجَ ʾakhraja 'he took (it) out' (I خَرَجَ kharaja 'he went out')."),
    ("V", "تَفَعَّلَ", "tafaʿʿala",
     "ta- + Form II — reflexive of II, done to oneself: تَعَلَّمَ taʿallama 'he learned' (II عَلَّمَ 'he taught')."),
    ("VI", "تَفَاعَلَ", "tafāʿala",
     "ta- + Form III — reciprocal, doing it to each other: تَكَاتَبَ takātaba '(they) wrote to each other'."),
    ("VII", "اِنْفَعَلَ", "infaʿala",
     "Prefix in- — passive/middle of I, happens by itself: اِنْكَسَرَ inkasara 'it broke, got broken' (I كَسَرَ kasara 'he broke')."),
    ("VIII", "اِفْتَعَلَ", "iftaʿala",
     "-ta- infixed after the first root letter — reflexive of I, often idiomatic: اِجْتَمَعَ ijtamaʿa '(they) gathered, met' (I جَمَعَ jamaʿa 'he collected')."),
    ("IX", "اِفْعَلَّ", "ifʿalla",
     "Doubled last root letter — colors and physical states: اِحْمَرَّ iḥmarra 'it turned red' (أَحْمَر ʾaḥmar 'red')."),
    ("X", "اِسْتَفْعَلَ", "istafʿala",
     "Prefix ista- — ask for, seek, or consider X: اِسْتَغْفَرَ istaghfara 'he asked forgiveness' (I غَفَرَ ghafara 'he forgave')."),
)


def arabic_form_note(form):
    """The (pattern, transliteration, note) for a form number ("I".."X"),
    or None for anything else (quadriliteral forms, non-verbs)."""
    for name, pattern, translit, note in AR_VERB_FORMS:
        if name == form:
            return pattern, translit, note
    return None

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
