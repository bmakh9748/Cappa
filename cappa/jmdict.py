"""Japanese word lookup: JMdict + deinflection. The dictionary IS the tokeniser.

Japanese writes no spaces, so something has to decide where a word ends. The
recogniser's own grouping cuts at the script boundary (kanji run, then kana
run), which is exactly where okurigana lives -- 戻る came apart as 戻 | るのも,
面白い as 面白 | い. That boundary is not approximately wrong, it is
anti-correlated with real word boundaries, and every fragment poisoned the
translation and the card.

So Cappa stops guessing at OCR time. Hotspots are one per CHARACTER, and the
word is resolved at LOOKUP time, the way Yomitan and 10ten do it: from the
clicked character, scan forward over progressively shorter substrings; test
each one against JMdict both as written and through every form it could be an
inflection of; the LONGEST substring that hits the dictionary is the word.
Clicking 戻 in 戻るのも面倒なんで finds 戻る, a Godan verb, and says so.

    resolve(text, index) -> Match(start, end, surface, base, entries, reasons)

Deinflection (_RULES) rewrites an inflected ending back toward the dictionary
form, carrying a set of part-of-speech types the result must have -- so って
unwinds to a Godan verb's う/つ/る but never to an ichidan one, and a chain
like やっといて -> やっとく -> やって -> やる is three rules deep. A candidate
matches an entry only when the types agree, which is what stops 来て resolving
to the noun 来.

DATA. JMdict is the property of the Electronic Dictionary Research and
Development Group, used under their licence (http://www.edrdg.org/edrdg/
licence.html) -- attribution is required and lives in the README and the
settings window. The pack is the jmdict-simplified JSON release (~11 MB),
downloaded once and converted to a stdlib sqlite3 database so lookups are
indexed and memory stays flat however big the dictionary gets. Same lazy,
fail-soft, no-pack-means-no-change contract as lexicon.py: without a pack
resolve() returns None and the caller falls back to the script run. Free,
key-less, offline after the first fetch, and NO LLM -- the same rule
translate.py and dictionary.py live by.

Pure data + sqlite; no Qt. ensure_pack() runs on the detection worker thread
beside lexicon.ensure_pack(); resolve() is called from the UI thread on hover
(cached by the caller) and from the card thread."""

import json
import os
import re
import sqlite3
import threading
import urllib.request

PACKS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "jmdict_packs")
DB_NAME = "jmdict-eng.sqlite3"
ZIP_NAME = "jmdict-eng.json.zip"   # kept so a rebuild costs no download
SCHEMA_VERSION = "2"

# Kanji spellings JMdict marks as rare, outdated, irregular or search-only.
# 「の」's entry lists 乃 and 之 as kanji; showing a particle as 乃 is
# nonsense, so the headword is the first spelling NOT tagged like this.
_RARE_KANJI = frozenset(("rK", "oK", "iK", "sK"))

# The jmdict-simplified releases carry the version in the asset name, so the
# download URL is resolved through the releases API rather than pinned.
_RELEASES = ("https://api.github.com/repos/scriptin/jmdict-simplified"
             "/releases/latest")
_ASSET = re.compile(r"^jmdict-eng-\d[^/]*\.json\.zip$")
_UA = "Cappa/0.1 (local language-learning flashcard app)"

# Only Japanese needs this: every other language Cappa reads has spaces, or
# (Chinese) has no inflection for these rules to unwind.
LANG = "ja"

MAX_SCAN = 12     # longest substring tried from the clicked character. The
                  # longest JMdict headwords that matter are compounds like
                  # 公衆電話 (4); 12 leaves room for a long inflected chain.
MAX_DEPTH = 5     # deinflection rules chained per candidate: やっといてくれ
                  # needs all five (imperative, -te kuru, -te, -te oku
                  # contraction, -te) to reach やる.
LOOKBACK = 8      # how far word_at() searches BACK for a word that covers
                  # the clicked character. Scanning only forward (which is
                  # all Yomitan does) answers a click on 倒 in 面倒 with the
                  # lone kanji 倒, 'reverse; inversion'. Cappa's hotspots are
                  # per character, so it can afford to ask which WORD the
                  # character is inside.

# Part-of-speech families a deinflection rule can demand. JMdict's own tags
# are finer (v5r, v5k, vs-i, ...); _family() folds them onto these.
V1 = "v1"          # ichidan verb        (食べる)
V5 = "v5"          # godan verb          (戻る, 行く)
VS = "vs"          # suru verb           (する, 勉強する)
VK = "vk"          # kuru                (来る)
ADJ = "adj-i"      # i-adjective         (面倒臭い)
TE = "te"          # the て-form itself — not a JMdict tag, an internal state
                   # that lets an auxiliary (ておく, ている) be peeled off
                   # before the て-form is unwound to a dictionary form.

# (inflected ending, dictionary-ward ending, types the INFLECTED form must
#  have, types the RESULT must have, reason shown to the user).
# `None` for types-in means "applies to anything".
_RULES = [
    # ---- auxiliaries hanging off a て-form. Peel these first; what is left
    # ---- still ends in て/で and unwinds through the te-form rules below.
    ("ている", "て", None, {TE}, "-te iru"),
    ("ています", "て", None, {TE}, "-te iru (polite)"),
    ("てる", "て", None, {TE}, "-te iru (contraction)"),
    ("ておく", "て", None, {TE}, "-te oku"),
    ("とく", "て", {V5}, {TE}, "-te oku (contraction)"),
    ("でおく", "で", None, {TE}, "-te oku"),
    ("どく", "で", {V5}, {TE}, "-te oku (contraction)"),
    ("てある", "て", None, {TE}, "-te aru"),
    ("てしまう", "て", None, {TE}, "-te shimau"),
    ("ちゃう", "て", {V5}, {TE}, "-te shimau (contraction)"),
    ("じゃう", "で", {V5}, {TE}, "-te shimau (contraction)"),
    ("ていく", "て", None, {TE}, "-te iku"),
    ("てくる", "て", None, {TE}, "-te kuru"),
    ("てくれる", "て", None, {TE}, "-te kureru"),
    ("てもらう", "て", None, {TE}, "-te morau"),
    ("てみる", "て", None, {TE}, "-te miru"),
    # ---- the て/た forms themselves -> dictionary form.
    # って and んで are AMBIGUOUS: って is the te-form of an う-, つ- and
    # る-verb alike. Nothing in the string says which, so the rules are
    # ordered by how common the row is and the first one that actually hits
    # the dictionary wins -- る before う before つ, because 分かった must
    # find 分かる ('to understand') and not 分かつ ('to divide'), while
    # 待った still finds 待つ (待る and 待う are not words).
    ("って", "る", {TE}, {V5}, "-te"),
    ("って", "う", {TE}, {V5}, "-te"),
    ("って", "つ", {TE}, {V5}, "-te"),
    ("いて", "く", {TE}, {V5}, "-te"),
    ("いで", "ぐ", {TE}, {V5}, "-te"),
    ("して", "す", {TE}, {V5}, "-te"),
    ("んで", "む", {TE}, {V5}, "-te"),
    ("んで", "ぶ", {TE}, {V5}, "-te"),
    ("んで", "ぬ", {TE}, {V5}, "-te"),
    ("て", "る", {TE}, {V1}, "-te"),
    ("きて", "くる", {TE}, {VK}, "-te"),
    ("して", "する", {TE}, {VS}, "-te"),
    ("った", "る", None, {V5}, "past"),
    ("った", "う", None, {V5}, "past"),
    ("った", "つ", None, {V5}, "past"),
    ("いた", "く", None, {V5}, "past"),
    ("いだ", "ぐ", None, {V5}, "past"),
    ("した", "す", None, {V5}, "past"),
    ("んだ", "む", None, {V5}, "past"),
    ("んだ", "ぶ", None, {V5}, "past"),
    ("んだ", "ぬ", None, {V5}, "past"),
    ("た", "る", None, {V1}, "past"),
    ("きた", "くる", None, {VK}, "past"),
    ("した", "する", None, {VS}, "past"),
    # ---- polite
    ("います", "う", None, {V5}, "polite"),
    ("きます", "く", None, {V5}, "polite"),
    ("ぎます", "ぐ", None, {V5}, "polite"),
    ("します", "す", None, {V5}, "polite"),
    ("ちます", "つ", None, {V5}, "polite"),
    ("にます", "ぬ", None, {V5}, "polite"),
    ("びます", "ぶ", None, {V5}, "polite"),
    ("みます", "む", None, {V5}, "polite"),
    ("ります", "る", None, {V5}, "polite"),
    ("ます", "る", None, {V1}, "polite"),
    ("きます", "くる", None, {VK}, "polite"),
    ("します", "する", None, {VS}, "polite"),
    # ---- negative
    ("わない", "う", None, {V5}, "negative"),
    ("かない", "く", None, {V5}, "negative"),
    ("がない", "ぐ", None, {V5}, "negative"),
    ("さない", "す", None, {V5}, "negative"),
    ("たない", "つ", None, {V5}, "negative"),
    ("なない", "ぬ", None, {V5}, "negative"),
    ("ばない", "ぶ", None, {V5}, "negative"),
    ("まない", "む", None, {V5}, "negative"),
    ("らない", "る", None, {V5}, "negative"),
    ("ない", "る", None, {V1}, "negative"),
    ("こない", "くる", None, {VK}, "negative"),
    ("しない", "する", None, {VS}, "negative"),
    ("なかった", "ない", None, None, "past negative"),
    # ---- potential / passive / causative fold onto ichidan, which the
    # ---- ichidan rules above then unwind (食べられる -> 食べられる(v1)).
    ("える", "う", {V1}, {V5}, "potential"),
    ("ける", "く", {V1}, {V5}, "potential"),
    ("げる", "ぐ", {V1}, {V5}, "potential"),
    ("せる", "す", {V1}, {V5}, "potential"),
    ("てる", "つ", {V1}, {V5}, "potential"),
    ("ねる", "ぬ", {V1}, {V5}, "potential"),
    ("べる", "ぶ", {V1}, {V5}, "potential"),
    ("める", "む", {V1}, {V5}, "potential"),
    ("れる", "る", {V1}, {V5}, "potential"),
    ("られる", "る", {V1}, {V1}, "potential/passive"),
    ("される", "する", {V1}, {VS}, "passive"),
    ("させる", "する", {V1}, {VS}, "causative"),
    # ---- conditional / volitional / imperative
    ("えば", "う", None, {V5}, "conditional"),
    ("けば", "く", None, {V5}, "conditional"),
    ("げば", "ぐ", None, {V5}, "conditional"),
    ("せば", "す", None, {V5}, "conditional"),
    ("てば", "つ", None, {V5}, "conditional"),
    ("ねば", "ぬ", None, {V5}, "conditional"),
    ("べば", "ぶ", None, {V5}, "conditional"),
    ("めば", "む", None, {V5}, "conditional"),
    ("れば", "る", None, {V5}, "conditional"),
    ("れば", "る", None, {V1}, "conditional"),
    ("おう", "う", None, {V5}, "volitional"),
    ("こう", "く", None, {V5}, "volitional"),
    ("ごう", "ぐ", None, {V5}, "volitional"),
    ("そう", "す", None, {V5}, "volitional"),
    ("とう", "つ", None, {V5}, "volitional"),
    ("のう", "ぬ", None, {V5}, "volitional"),
    ("ぼう", "ぶ", None, {V5}, "volitional"),
    ("もう", "む", None, {V5}, "volitional"),
    ("ろう", "る", None, {V5}, "volitional"),
    ("よう", "る", None, {V1}, "volitional"),
    ("え", "う", None, {V5}, "imperative"),
    ("け", "く", None, {V5}, "imperative"),
    ("せ", "す", None, {V5}, "imperative"),
    ("て", "つ", None, {V5}, "imperative"),
    ("ね", "ぬ", None, {V5}, "imperative"),
    ("べ", "ぶ", None, {V5}, "imperative"),
    ("め", "む", None, {V5}, "imperative"),
    ("れ", "る", None, {V5}, "imperative"),
    ("ろ", "る", None, {V1}, "imperative"),
    ("れ", "れる", None, {V1}, "imperative"),   # くれ -> くれる
    ("たい", "る", None, {V1}, "-tai"),
    ("いたい", "う", None, {V5}, "-tai"),
    ("きたい", "く", None, {V5}, "-tai"),
    ("りたい", "る", None, {V5}, "-tai"),
    # ---- i-adjectives
    ("かった", "い", None, {ADJ}, "past"),
    ("くない", "い", None, {ADJ}, "negative"),
    ("くて", "い", None, {ADJ}, "-te"),
    ("く", "い", None, {ADJ}, "adverbial"),
    ("ければ", "い", None, {ADJ}, "conditional"),
    ("さ", "い", None, {ADJ}, "noun form"),
]

# JMdict part-of-speech tag -> the family the rules speak in.
def _family(tag):
    if tag == "v1" or tag.startswith("v1-"):
        return V1
    if tag.startswith("v5"):
        return V5
    if tag == "vk":
        return VK
    if tag.startswith("vs"):
        return VS
    if tag.startswith("adj-i"):
        return ADJ
    return None


# Readable labels for the tags a popup shows. Anything unlisted prints raw.
POS_LABELS = {
    "v1": "Ichidan verb", "v5u": "Godan verb (-u)",
    "v5k": "Godan verb (-ku)", "v5g": "Godan verb (-gu)",
    "v5s": "Godan verb (-su)", "v5t": "Godan verb (-tsu)",
    "v5n": "Godan verb (-nu)", "v5b": "Godan verb (-bu)",
    "v5m": "Godan verb (-mu)", "v5r": "Godan verb (-ru)",
    "v5k-s": "Godan verb (iku/yuku)", "v5r-i": "Godan verb (irregular)",
    "v5aru": "Godan verb (-aru)",
    "vk": "Kuru verb", "vs": "Suru verb", "vs-i": "Suru verb (irregular)",
    "vs-s": "Suru verb (-su)",
    "vt": "transitive", "vi": "intransitive",
    "adj-i": "I-adjective", "adj-ix": "I-adjective (yoi/ii)",
    "adj-na": "Na-adjective", "adj-no": "No-adjective",
    "adj-pn": "Pre-noun adjectival",
    "n": "Noun", "n-suf": "Noun suffix", "n-pref": "Noun prefix",
    "adv": "Adverb", "adv-to": "Adverb (-to)",
    "pn": "Pronoun", "prt": "Particle", "conj": "Conjunction",
    "int": "Interjection", "exp": "Expression", "aux": "Auxiliary",
    "aux-v": "Auxiliary verb", "aux-adj": "Auxiliary adjective",
    "cop": "Copula", "ctr": "Counter", "num": "Numeric", "pref": "Prefix",
    "suf": "Suffix", "unc": "Unclassified",
}


class Entry:
    """One dictionary entry: a headword, its reading, and its senses."""

    __slots__ = ("headword", "reading", "senses", "common")

    def __init__(self, headword, reading, senses, common):
        self.headword = headword
        self.reading = reading
        # [(pos_tags, glosses)] in JMdict's own sense order.
        self.senses = senses
        self.common = common

    def tags(self):
        """The first sense's parts of speech, as readable labels."""
        if not self.senses:
            return []
        return [POS_LABELS.get(t, t) for t in self.senses[0][0]]

    def __repr__(self):
        return "Entry(%r, %r, %d senses)" % (
            self.headword, self.reading, len(self.senses))


class Match:
    """What the click resolved to: which characters, and what they mean."""

    __slots__ = ("start", "end", "surface", "base", "entries", "reasons")

    def __init__(self, start, end, surface, base, entries, reasons):
        self.start = start          # char offsets into the LINE text
        self.end = end
        self.surface = surface      # the characters on screen: 戻って
        self.base = base            # the dictionary form found: 戻る
        self.entries = entries      # [Entry], best first
        self.reasons = reasons      # ("-te",) — how surface became base

    @property
    def entry(self):
        return self.entries[0] if self.entries else None

    def __repr__(self):
        return "Match(%r -> %r, %d entries)" % (
            self.surface, self.base, len(self.entries))


# --------------------------------------------------------------- the pack
_lock = threading.Lock()
_conn = None          # sqlite connection, or None
_looked = False       # have we tried to open it yet

_CACHE_MAX = 512
_resolved = {}        # (line text, char index) -> Match or None


def _db_path():
    return os.path.normpath(os.path.join(PACKS_DIR, DB_NAME))


def _open():
    """The pack's connection, or None. Opened once; a missing pack is
    remembered so we don't stat the disk on every hover."""
    global _conn, _looked
    with _lock:
        if _conn is not None or _looked:
            return _conn
        _looked = True
        path = _db_path()
        if not os.path.isfile(path):
            return None
        try:
            conn = sqlite3.connect(path, check_same_thread=False)
            version = conn.execute(
                "SELECT v FROM meta WHERE k='schema'").fetchone()
            if not version or version[0] != SCHEMA_VERSION:
                conn.close()
                return None
        except Exception:
            return None
        _conn = conn
        return _conn


def ready():
    """Whether Japanese lookup is available right now."""
    return _open() is not None


def ensure_pack(lang, timeout=120.0):
    """Download and build the JMdict pack if it isn't there yet. Lazy and
    fail-soft, exactly like lexicon.ensure_pack: returns True when a usable
    pack is present afterwards. Safe on a worker thread; never raises."""
    if lang != LANG:
        return False
    if ready():
        return True
    try:
        os.makedirs(PACKS_DIR, exist_ok=True)
        raw = _download(timeout)
        if raw is None:
            return False
        _build(raw)
    except Exception as exc:
        print("[cappa] jmdict: pack build failed: %s" % exc)
        return False
    global _looked
    with _lock:
        _looked = False       # force a re-open of the new file
    ok = ready()
    print("[cappa] jmdict: pack %s" % ("ready" if ok else "FAILED"))
    return ok


def _download(timeout):
    """The jmdict-eng release zip, as bytes. Kept on disk: rebuilding the
    database after a schema change must not re-download 11 MB."""
    cached = os.path.normpath(os.path.join(PACKS_DIR, ZIP_NAME))
    if os.path.isfile(cached):
        with open(cached, "rb") as f:
            return f.read()
    req = urllib.request.Request(_RELEASES, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        release = json.load(r)
    asset = next((a for a in release.get("assets", [])
                  if _ASSET.match(a.get("name", ""))), None)
    if asset is None:
        print("[cappa] jmdict: no jmdict-eng asset in the latest release")
        return None
    print("[cappa] jmdict: downloading %s (%.1f MB)"
          % (asset["name"], asset["size"] / 1e6))
    req = urllib.request.Request(asset["browser_download_url"],
                                 headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    tmp = cached + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, cached)
    return data


def _build(zipped):
    """Convert the release zip into the sqlite pack, atomically."""
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(zipped)) as z:
        name = next(n for n in z.namelist() if n.endswith(".json"))
        with z.open(name) as f:
            data = json.load(f)

    path = _db_path()
    tmp = path + ".part"
    if os.path.exists(tmp):
        os.remove(tmp)
    conn = sqlite3.connect(tmp)
    try:
        conn.executescript("""
            CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
            CREATE TABLE entries (
                id INTEGER PRIMARY KEY, headword TEXT, reading TEXT,
                common INTEGER, senses TEXT);
            CREATE TABLE lookup (key TEXT NOT NULL, id INTEGER NOT NULL);
        """)
        conn.execute("INSERT INTO meta VALUES ('schema', ?)",
                     (SCHEMA_VERSION,))
        conn.execute("INSERT INTO meta VALUES ('version', ?)",
                     (str(data.get("version", "")),))
        rows, keys = [], []
        for i, word in enumerate(data.get("words", [])):
            kanji = word.get("kanji") or []
            kana = word.get("kana") or []
            senses = []
            for sense in word.get("sense") or []:
                glosses = [g["text"] for g in sense.get("gloss") or []
                           if g.get("lang", "eng") == "eng"]
                if glosses:
                    senses.append([sense.get("partOfSpeech") or [], glosses])
            if not senses:
                continue
            common_kanji = [k for k in kanji
                            if not (_RARE_KANJI & set(k.get("tags") or ()))]
            # Kana BEFORE the rare kanji: when every kanji spelling is
            # rare/search-only the kana is the word (の, whose only kanji
            # 乃 and 之 are both tagged sK).
            headword = (common_kanji or kana or kanji
                        or [{"text": ""}])[0]["text"]
            reading = kana[0]["text"] if kana else ""
            common = any(k.get("common") for k in kanji) or \
                any(k.get("common") for k in kana)
            rows.append((i, headword, reading, int(common),
                         json.dumps(senses, ensure_ascii=False)))
            for form in kanji + kana:
                keys.append((form["text"], i))
        conn.executemany("INSERT INTO entries VALUES (?,?,?,?,?)", rows)
        conn.executemany("INSERT INTO lookup VALUES (?,?)", keys)
        conn.execute("CREATE INDEX idx_lookup ON lookup(key)")
        conn.commit()
    finally:
        conn.close()
    os.replace(tmp, path)
    print("[cappa] jmdict: built %s (%d entries, %d keys)"
          % (DB_NAME, len(rows), len(keys)))


# ---------------------------------------------------------------- lookup
def _entries_for(form):
    """Every entry keyed by exactly `form`, best first.

    An entry whose HEADWORD is the queried form outranks one that merely
    lists it among its other spellings: 本 keys both 本 ('book') and もと
    ('origin', spelled 元/本/素/基), and only the headword test tells them
    apart. Likewise に keys the particle and 荷 ('baggage'). Commonness
    breaks the remaining ties."""
    conn = _open()
    if conn is None:
        return []
    with _lock:
        rows = conn.execute(
            "SELECT e.headword, e.reading, e.senses, e.common "
            "FROM lookup l JOIN entries e ON e.id = l.id "
            "WHERE l.key = ? "
            "ORDER BY (e.headword = ?) DESC, e.common DESC, e.id",
            (form, form)
        ).fetchall()
    return [Entry(h, r, [(tuple(p), tuple(g)) for p, g in json.loads(s)], c)
            for h, r, s, c in rows]


def _to_hiragana(text):
    """Katakana folded to hiragana. Hardsubs shout in katakana (オマエ) where
    JMdict keys the word under its kana reading (おまえ -> お前), and the
    deinflection rules are written in hiragana too."""
    return "".join(
        chr(ord(ch) - 0x60) if 0x30A1 <= ord(ch) <= 0x30F6 else ch
        for ch in text)


def _deinflect(text):
    """`text` plus every form it could be an inflection of, as
    [(form, reasons, types)] -- types is the set of part-of-speech families
    the form must belong to, or None for 'anything'. Breadth-first so the
    shallowest (most likely) derivation of a form is the one kept."""
    out = [(text, (), None)]
    # Keyed by form AND the types demanded of it: れば -> る is both a godan
    # and an ichidan conditional, and keying on the stem alone would drop
    # whichever rule came second.
    seen = {(text, None)}
    queue = [(text, (), None)]
    kana = _to_hiragana(text)
    if kana != text:
        seen.add((kana, None))
        out.append((kana, (), None))
        queue.append((kana, (), None))
    while queue:
        form, reasons, types = queue.pop(0)
        if len(reasons) >= MAX_DEPTH:
            continue
        for tail, base, want_in, want_out, reason in _RULES:
            if not form.endswith(tail):
                continue
            if want_in is not None and types is not None \
                    and not (types & want_in):
                continue
            stem = form[:len(form) - len(tail)] + base
            key = (stem, frozenset(want_out) if want_out else None)
            if not stem or key in seen:
                continue
            seen.add(key)
            item = (stem, reasons + (reason,), want_out)
            out.append(item)
            queue.append(item)
    return out


def _matches(types, entry):
    """Does this entry's part of speech satisfy the deinflection's demand?"""
    if types is None:
        return True
    for pos, _glosses in entry.senses:
        for tag in pos:
            if _family(tag) in types:
                return True
    return False


def lookup(form):
    """Entries for an exact dictionary form; [] when absent or no pack."""
    return _entries_for(form)


def resolve(text, index):
    """The word at character `index` of `text`, or None.

    Scans forward from `index` over substrings, longest first; each is
    deinflected and looked up, and the first (longest) substring with a
    type-consistent entry wins. That is what turns a click on 戻 into 戻る
    rather than 戻, and a click on 面 into 面倒.

    Cached: the overlay calls this for the hovered character on every tick,
    and one resolve is hundreds of indexed queries."""
    if _open() is None or not text or not (0 <= index < len(text)):
        return None
    key = (text, index)
    if key in _resolved:
        return _resolved[key]
    match = None
    limit = min(len(text), index + MAX_SCAN)
    for end in range(limit, index, -1):
        surface = text[index:end]
        for form, reasons, types in _deinflect(surface):
            if types and TE in types:
                continue   # a bare て-form is a waypoint, never a headword
            entries = [e for e in _entries_for(form) if _matches(types, e)]
            if entries:
                match = Match(index, end, surface, form, entries, reasons)
                break
        if match is not None:
            break
    if len(_resolved) >= _CACHE_MAX:
        _resolved.clear()
    _resolved[key] = match
    return match


def word_at(text, index, lookback=LOOKBACK):
    """The word CONTAINING character `index`, or None. The UI's entry point.

    resolve() only scans forward, so a click on the second half of a word
    answers with whatever starts there -- 倒 in 面倒 comes back as the lone
    kanji 'reverse; inversion'. Here every start from `index` back to
    `index - lookback` is resolved, the matches that actually cover `index`
    are kept, and the LONGEST wins: 話 inside 公衆電話 finds the whole
    compound rather than 電話 or 話. Ties go to the match starting nearest
    the click, which is the one the user pointed at."""
    if _open() is None or not text or not (0 <= index < len(text)):
        return None
    best = None
    for start in range(index, max(-1, index - lookback - 1), -1):
        match = resolve(text, start)
        if match is None or match.end <= index:
            continue      # nothing here, or it stops before the click
        if best is None or (match.end - match.start) > (best.end - best.start):
            best = match
    return best


def close():
    """Release the pack (tests reopen it)."""
    global _conn, _looked
    _resolved.clear()
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None
        _looked = False
