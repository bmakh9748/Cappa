"""Arabic word anatomy: root, verb form, vocalized lemma — offline.

The Grammar tab's Arabic half. A clicked word — usually undiacritized,
often carrying clitics (والكتاب 'and-the-book') — is analyzed by CAMeL
Tools' morphological analyzer against the calima-msa-r13 database:

    analyze("استغفر") -> lemma ٱِسْتَغْفَر, root غ.ف.ر, Form X, verb,
                         gloss 'beg forgiveness'

The Form I-X classification is not in the database's output; verb_form()
derives it from the vocalized lemma's letter skeleton (verified against
the classical patterns). The English stemgloss rides along free — an
offline mini-definition.

DATA. camel-tools itself is MIT and installed SLIM (--no-deps + six,
pyrsistent, cachetools, emoji, tqdm, muddler — see requirements.txt; the
full install drags in ~1 GB of torch/transformers that only the neural
disambiguators need). The database is the GPL-2 morphology-db-msa-r13
release (40.5 MB zip -> 38.6 MB morphology.db in arabic_packs/, LICENSE
kept beside it), downloaded once from the CAMeL Lab's GitHub releases and
sha256-checked — the jmdict pack contract: lazy, fail-soft,
no-pack-means-no-change. Ranking uses the database's own pos_lex_logprob
(measured better than the 89 MB MLE disambiguator on our samples — no
extra model, no torch).

COST. The analyzer holds ~400 MB of RAM once loaded and takes ~2 s to
load; _load() runs lazily on the FIRST analyze() call, so callers keep
that call off the UI thread (the popup's grammar fetch already is).
After that a word costs well under a millisecond. No Qt."""

import hashlib
import os
import re
import threading
import urllib.request
import zipfile

PACKS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "..", "..", "arabic_packs")
DB_NAME = "morphology.db"
LANG = "ar"

# The release asset is pinned (URL + sha256) rather than resolved from a
# latest-release API: the data revision matters (r13 is the free GPL one;
# its sibling s31 is LDC-restricted and must never be fetched).
_URL = ("https://github.com/CAMeL-Lab/camel-tools-data/releases/download/"
        "2022.03.21/morphology_db_calima-msa-r13-0.4.0.zip")
_SHA256 = "fe6531250c5529307627cc63ed56447cbb9968020d6ea3ab867e6ad9af94c738"
_UA = "Cappa/0.1 (local language-learning flashcard app)"

_lock = threading.Lock()
_analyzer = None
_failed = False    # camel-tools missing/broken: remembered so a popup
                   # doesn't re-attempt the import on every click

# Harakat stripped when reducing a lemma to its letter skeleton. Deliberately
# NOT a character range: a range from fathatan to sukun would eat the shadda
# (U+0651), which verb_form() must SEE (it doubles a letter).
_HARAKAT = re.compile("[ًٌٍَُِْٰ]")
_SHADDA = "ّ"


class Analysis:
    """One word's morphological reading, display-ready."""

    __slots__ = ("lemma", "root", "form", "pos", "gloss", "loan")

    def __init__(self, lemma, root, form, pos, gloss, loan):
        self.lemma = lemma   # vocalized dictionary form (wasla normalized)
        self.root = root     # space-joined letters ("ك ت ب"); "" for a
                             # loanword ('#' marks a weak radical)
        self.form = form     # "I".."X" for verbs, else None
        self.pos = pos       # noun / verb / adj / noun_prop / ...
        self.gloss = gloss   # the database's English mini-definition
        self.loan = loan     # True when the word has no Arabic root

    def __repr__(self):
        return "Analysis(%r, root=%r, form=%r)" % (
            self.lemma, self.root, self.form)


def _db_path():
    return os.path.normpath(os.path.join(PACKS_DIR, DB_NAME))


def ready():
    """Whether the morphology database is on disk (the analyzer itself
    loads lazily on first use)."""
    return os.path.isfile(_db_path())


def ensure_pack(lang, timeout=300.0):
    """Download the morphology database if it isn't there yet. Lazy,
    fail-soft, never raises — the jmdict.ensure_pack contract. The zip is
    NOT kept after extraction: unlike the jmdict packs there is no rebuild
    step that could reuse it, and it is 40 MB."""
    if lang != LANG:
        return False
    if ready():
        return True
    try:
        import camel_tools  # noqa: F401 — is there an analyzer to feed?
    except ImportError:
        print("[cappa] arabic: camel-tools not installed — morphology "
              "pack skipped (see requirements.txt)")
        return False
    try:
        os.makedirs(PACKS_DIR, exist_ok=True)
        print("[cappa] arabic: downloading morphology db (40 MB)")
        req = urllib.request.Request(_URL, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
        if hashlib.sha256(data).hexdigest() != _SHA256:
            print("[cappa] arabic: morphology db checksum mismatch")
            return False
        zpath = _db_path() + ".zip.part"
        with open(zpath, "wb") as f:
            f.write(data)
        with zipfile.ZipFile(zpath) as z:
            for name in z.namelist():
                base = os.path.basename(name)
                # morphology_db/calima-msa-r13/{morphology.db, LICENSE}
                if base == DB_NAME or base == "LICENSE":
                    target = os.path.normpath(
                        os.path.join(PACKS_DIR, base))
                    with z.open(name) as src, open(target + ".part",
                                                   "wb") as dst:
                        dst.write(src.read())
                    os.replace(target + ".part", target)
        try:
            os.remove(zpath)
        except OSError:
            pass   # an AV scanner holding the zip must not fail a pack
                   # that is already fully installed
    except Exception as exc:
        print("[cappa] arabic: pack fetch failed: %s" % exc)
        return False
    ok = ready()
    print("[cappa] arabic: morphology db %s" % ("ready" if ok else "FAILED"))
    return ok


def _load():
    """The analyzer, constructed once; None when the pack or the library
    is absent. Loading costs ~2 s and ~400 MB — first use only, and the
    popup's grammar thread is who pays it."""
    global _analyzer, _failed
    with _lock:
        if _analyzer is not None or _failed:
            return _analyzer
        if not ready():
            return None   # pack may still arrive; don't latch failure
        try:
            from camel_tools.morphology.analyzer import Analyzer
            from camel_tools.morphology.database import MorphologyDB
            _analyzer = Analyzer(MorphologyDB(_db_path()))
        except Exception as exc:
            print("[cappa] arabic: analyzer unavailable: %s" % exc)
            _failed = True
            return None
        return _analyzer


def _skeleton(lemma):
    """The lemma's bare letters: harakat stripped, wasla/madda normalized,
    shadda expanded to a doubled letter — what the form patterns see."""
    s = _HARAKAT.sub("", lemma)
    s = s.replace("ٱ", "ا")            # ٱ (wasla) -> ا
    s = s.replace("آ", "اا")      # آ (madda) -> اا
    out = []
    for ch in s:
        if ch == _SHADDA:
            if out:
                out.append(out[-1])
        else:
            out.append(ch)
    return "".join(out)


def _radical_eq(radical, letter):
    """Does a root radical match a skeleton letter ('#', the database's
    weak-radical placeholder, matches anything)?"""
    return radical == "#" or radical == letter


def verb_form(lemma, root=""):
    """The classical Form ("I".."X") of a vocalized verb lemma, or None
    when the shape is not one of the ten (quadriliterals, junk). Pattern
    classification over the letter skeleton; `root` (the analyzer's dotted
    root, when known) breaks the one tie shapes can't (IX vs an
    assimilated Form VIII geminate).

    Check order is load-bearing:
    the VIII shape runs before VII because انتظر (iftaʿala, root ن ظ ر)
    also starts with ان; the doubled-middle II test runs before IV
    because أَكَّد (faʿʿala, hamza-initial root) also starts with أ."""
    s = _skeleton(lemma)
    n = len(s)
    if n == 3:
        return "I"
    radicals = [c for c in (root or "").replace(".", "")]
    if n == 6 and s.startswith("است"):        # است...
        return "X"
    if n == 5:
        if s[0] == "ا" and s[2] == "ت":            # ا?ت... (before VII: no
            return "VIII"                          # ت-initial Form VII in MSA)
        if s.startswith("ان"):                     # ان...
            return "VII"
        if s[0] == "ت" and s[2] == s[3]:                # ت + doubled
            return "V"
        if s[0] == "ت" and s[2] == "ا":            # ت?ا...
            return "VI"
        if s[0] == "ا" and s[3] == s[4]:                # ا...doubled
            # IX's tail-double shares this skeleton with an assimilated-ت
            # Form VIII geminate (اِضْطَرّ -> اضطرر, root ض ر ر). IX's own
            # radicals sit at s[1:3]; a root that disagrees means the
            # assimilated VIII, which has no honest shape label — None.
            if len(radicals) < 2 or (_radical_eq(radicals[0], s[1])
                                     and _radical_eq(radicals[1], s[2])):
                return "IX"
            return None
        return None
    if n == 4:
        if s[1] == s[2]:                                     # doubled middle
            return "II"  # (before IV: a true IV never doubles this position)
        if s[0] == "أ":                                 # أ...
            return "IV"
        if s[0] == "ا" and s[1] == "ا":            # آ fused from ʾa+ā:
            return "IV"                                 # آمَن, hamza-initial
                                                        # IV (true III of a
                                                        # hamza root is rare
                                                        # enough to concede)
        if s[1] == "ا":                                 # ?ا..
            return "III"
    return None


# The ten verb forms (awzān) on the root ف-ع-ل, in order. Rows are
# (form, pattern, transliteration, note): verb_form() above answers "X",
# this table explains what X is. ʿ = ayn, ʾ = hamza. Voice: function
# first, then a tiny example with gloss — accuracy over coverage.
VERB_FORMS = (
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


def form_note(form):
    """The (pattern, transliteration, note) for a form number ("I".."X"),
    or None for anything else (quadriliteral forms, non-verbs)."""
    for name, pattern, translit, note in VERB_FORMS:
        if name == form:
            return pattern, translit, note
    return None


def status():
    """"" when the analyzer can answer; else a short displayable reason
    for the popup — the difference between 'this word has no notes' and
    'the machinery isn't here yet' must be visible (fail soft, but say
    so). Meaningful after an analyze() attempt, which is when _failed is
    settled."""
    if not ready():
        return "Arabic morphology pack not downloaded yet"
    if _failed:
        return "camel-tools not installed (see requirements.txt)"
    return ""


def analyze(word):
    """The best morphological reading of an Arabic word, or None (no pack,
    no camel-tools, unknown word — the tab shows nothing, fail soft).
    Blocking on first call (database load); cheap after."""
    analyzer = _load()
    if analyzer is None or not word:
        return None
    try:
        analyses = analyzer.analyze(word)
    except Exception:
        return None
    if not analyses:
        return None
    best = max(analyses, key=lambda a: a.get("pos_lex_logprob", -99.0))
    lemma = (best.get("lex") or "").replace("ٱ", "ا")
    raw_root = best.get("root") or ""
    loan = raw_root in ("", "NTWS")   # the db's no-root marker: loanwords
    root = "" if loan else " ".join(raw_root.split("."))
    pos = best.get("pos") or ""
    form = (verb_form(lemma, "" if loan else raw_root)
            if pos == "verb" else None)
    gloss = (best.get("stemgloss") or "").replace("_", " ").replace(
        ";", "; ")
    return Analysis(lemma, root, form, pos, gloss, loan)


def close():
    """Release the analyzer (tests, language switches — frees ~400 MB)."""
    global _analyzer, _failed
    with _lock:
        _analyzer = None
        _failed = False
