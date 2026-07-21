"""Per-kanji information: meanings, readings, strokes, grade, JLPT level.

The Grammar tab's Japanese half. A clicked word like 面倒 breaks down into
its characters, and each character answers from KANJIDIC2: English
meanings, on/kun readings, stroke count, school grade and (old-scale) JLPT
level — the numbers a learner uses to decide how urgent a kanji is.

DATA. KANJIDIC2 is the KANJIDIC Project's (EDRDG) dictionary, CC BY-SA 4.0
— attribution lives in the README beside the JMdict credit. The pack is
the kanjidic2-en release of jmdict-simplified (~1.25 MB zip, 10,384
characters), the SAME GitHub releases Cappa's JMdict pack downloads from,
converted once to sqlite. Same lazy, fail-soft, no-pack-means-no-change
contract as jmdict.py: without a pack kanji_info() returns None and the
popup simply shows no kanji rows. Free, key-less, offline after the first
fetch, NO LLM.

Pure data + sqlite; no Qt. ensure_pack() runs on the detection worker
thread beside jmdict.ensure_pack(); kanji_info() is indexed-PK cheap."""

import json
import os
import re
import sqlite3
import threading
import urllib.request

from .jmdict import LANG, PACKS_DIR

DB_NAME = "kanjidic2-en.sqlite3"
ZIP_NAME = "kanjidic2-en.json.zip"   # kept so a rebuild costs no download
SCHEMA_VERSION = "1"

_RELEASES = ("https://api.github.com/repos/scriptin/jmdict-simplified"
             "/releases/latest")
# The -all variant also exists (adds non-English glosses) — not matched.
_ASSET = re.compile(r"^kanjidic2-en-\d[^/]*\.json\.zip$")
_UA = "Cappa/0.1 (local language-learning flashcard app)"

_lock = threading.Lock()
_conn = None
_looked = False


class Kanji:
    """One character's card: what it means, how it reads, how big it is."""

    __slots__ = ("literal", "grade", "jlpt", "strokes", "freq",
                 "onyomi", "kunyomi", "meanings", "nanori")

    def __init__(self, literal, grade, jlpt, strokes, freq,
                 onyomi, kunyomi, meanings, nanori):
        self.literal = literal
        self.grade = grade       # 1-6 kyōiku, 8 secondary jōyō, 9/10 names;
                                 # None off the school lists
        self.jlpt = jlpt         # OLD 4-level JLPT scale (pre-2010); None
                                 # when unlisted
        self.strokes = strokes
        self.freq = freq         # newspaper rank 1..~2501, None past that
        self.onyomi = onyomi     # (メン, ベン)
        self.kunyomi = kunyomi   # (おも, おもて, …) — '.' splits okurigana
        self.meanings = meanings
        self.nanori = nanori     # name-only readings

    def __repr__(self):
        return "Kanji(%r, %d meanings)" % (self.literal, len(self.meanings))


def _db_path():
    return os.path.normpath(os.path.join(PACKS_DIR, DB_NAME))


def _open():
    """The pack's connection, or None — jmdict.py's open-once contract."""
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
    """Whether kanji lookup is available right now."""
    return _open() is not None


def ensure_pack(lang, timeout=120.0):
    """Download and build the KANJIDIC2 pack if it isn't there yet. Lazy,
    fail-soft, never raises — jmdict.ensure_pack's contract exactly."""
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
        print("[cappa] kanjidic: pack build failed: %s" % exc)
        return False
    global _looked
    with _lock:
        _looked = False
    ok = ready()
    print("[cappa] kanjidic: pack %s" % ("ready" if ok else "FAILED"))
    return ok


def _download(timeout):
    """The kanjidic2-en release zip, as bytes (cached on disk — a schema
    rebuild must not re-download even 1.25 MB)."""
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
        print("[cappa] kanjidic: no kanjidic2-en asset in the latest "
              "release")
        return None
    print("[cappa] kanjidic: downloading %s (%.1f MB)"
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
            CREATE TABLE kanji (
                literal TEXT PRIMARY KEY, grade INTEGER, jlpt INTEGER,
                strokes INTEGER, freq INTEGER, onyomi TEXT, kunyomi TEXT,
                meanings TEXT, nanori TEXT);
        """)
        conn.execute("INSERT INTO meta VALUES ('schema', ?)",
                     (SCHEMA_VERSION,))
        conn.execute("INSERT INTO meta VALUES ('version', ?)",
                     (str(data.get("databaseVersion", "")),))
        rows = []
        for ch in data.get("characters", []):
            literal = ch.get("literal")
            if not literal:
                continue
            misc = ch.get("misc") or {}
            # strokeCounts[0] is the accepted count; the rest are common
            # miscounts KANJIDIC keeps for search.
            strokes = (misc.get("strokeCounts") or [None])[0]
            onyomi, kunyomi, meanings = [], [], []
            rm = ch.get("readingMeaning") or {}
            for group in rm.get("groups") or []:
                for reading in group.get("readings") or []:
                    if reading.get("type") == "ja_on":
                        onyomi.append(reading.get("value", ""))
                    elif reading.get("type") == "ja_kun":
                        kunyomi.append(reading.get("value", ""))
                for meaning in group.get("meanings") or []:
                    if meaning.get("lang", "en") == "en":
                        meanings.append(meaning.get("value", ""))
            if not meanings:
                continue   # a glyph with nothing to teach has no row
            rows.append((literal, misc.get("grade"), misc.get("jlptLevel"),
                         strokes, misc.get("frequency"),
                         json.dumps(onyomi, ensure_ascii=False),
                         json.dumps(kunyomi, ensure_ascii=False),
                         json.dumps(meanings, ensure_ascii=False),
                         json.dumps(rm.get("nanori") or [],
                                    ensure_ascii=False)))
        conn.executemany("INSERT INTO kanji VALUES (?,?,?,?,?,?,?,?,?)",
                         rows)
        conn.commit()
    finally:
        conn.close()
    os.replace(tmp, path)
    print("[cappa] kanjidic: built %s (%d characters)" % (DB_NAME, len(rows)))


def kanji_info(char):
    """The Kanji for one character, or None (kana, latin, no pack — the
    caller shows nothing, same contract as jmdict.resolve)."""
    conn = _open()
    if conn is None or not char:
        return None
    with _lock:
        row = conn.execute(
            "SELECT literal, grade, jlpt, strokes, freq, onyomi, kunyomi,"
            " meanings, nanori FROM kanji WHERE literal = ?",
            (char,)).fetchone()
    if row is None:
        return None
    lit, grade, jlpt, strokes, freq, on, kun, meanings, nanori = row
    return Kanji(lit, grade, jlpt, strokes, freq,
                 tuple(json.loads(on)), tuple(json.loads(kun)),
                 tuple(json.loads(meanings)), tuple(json.loads(nanori)))


def breakdown(word):
    """The word's unique kanji in reading order, each with its info.
    Callers pass the HEADWORD (the surface can be kana)."""
    out = []
    for ch in dict.fromkeys(word or ""):
        info = kanji_info(ch)
        if info is not None:
            out.append(info)
    return out


def close():
    """Release the pack (tests reopen it)."""
    global _conn, _looked
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None
        _looked = False
