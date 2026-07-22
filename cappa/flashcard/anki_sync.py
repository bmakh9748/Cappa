"""Puts each saved card into Anki -- sync(), called by the Create Anki
card button right after the draft saves. There is no export step.

Anki OPEN: the card goes through the AnkiConnect add-on's localhost API
and appears in the running app immediately. Anki CLOSED: it's written
into collection.anki2 directly with the official `anki` package (imported
lazily) and is there on next launch. The route is picked per call by
probing AnkiConnect's port.

Delivery is per-card and once: a delivered folder gets an anki_synced.txt
receipt and is never touched again, so whatever the user edits or deletes
IN Anki stays that way.
A failed delivery leaves no receipt and simply rides the next save; a
card already in Anki (found by its cappa::card_NNNN tag) is adopted, not
duplicated. Both routes write the same tag, notetype name ("Cappa card")
and per-card media names, so they stay interchangeable.

Qt-free; the UI calls sync() from a worker thread."""

import base64
import json
import os
import shutil
import tempfile
import time
import urllib.request

from . import prefs
from .template import ANKI_FIELD_NAMES
from .. import settings

FIELD_NAMES = list(ANKI_FIELD_NAMES.values())
NOTETYPE = "Cappa card"        # created once from the current design
MARKER = "anki_synced.txt"     # per-folder delivery receipt
URL = "http://127.0.0.1:8765"  # AnkiConnect's fixed port
_TIMEOUT = 15        # per live request once a sync is underway (media)
_PROBE_TIMEOUT = 1   # is-Anki-open check; localhost answers fast or never
_MAX_BACKUPS = 3     # rotating pre-write copies of collection.anki2


class SyncError(Exception):
    """Sync couldn't reach Anki at all -- never raised mid-write. The
    message is written for the user, not a developer."""


def deck_name(source_language=None):
    """'Cappa <Language>' -- one deck per language being learnt; plain
    'Cappa' when the video language is left on auto-detect."""
    code = source_language
    if code is None:
        code = settings.load().source_language
    name = dict(settings.SOURCE_LANGUAGES).get(code)
    return "Cappa %s" % name if name and code != "auto" else "Cappa"


# --------------------------------------------------------- card folders
def _pending_dirs(cards_dir):
    """Card folders with no delivery receipt yet -- normally just the card
    that was just saved. A card already delivered is the user's to edit or
    delete in Anki, never re-swept or resurrected."""
    if not os.path.isdir(cards_dir):
        return []
    return sorted(
        os.path.join(cards_dir, name) for name in os.listdir(cards_dir)
        if name.startswith("card_")
        and os.path.isdir(os.path.join(cards_dir, name))
        and not os.path.isfile(os.path.join(cards_dir, name, MARKER)))


def _mark_delivered(folder, route):
    """The receipt. Its existence is the flag; the content is for humans.
    Deleting it forces that one card to sync again."""
    try:
        with open(os.path.join(folder, MARKER), "w", encoding="utf-8",
                  newline="\n") as f:
            f.write("%s via %s\n"
                    % (time.strftime("%Y-%m-%d %H:%M:%S"), route))
    except OSError as exc:
        print("[cappa] anki sync: couldn't mark %s delivered: %s"
              % (os.path.basename(folder), exc))


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _card_fields(folder):
    word = _read(os.path.join(folder, "word.txt"))
    sentence = _read(os.path.join(folder, "sentence.txt"))
    if not word and not sentence:
        return None   # a failed draft -- nothing worth writing
    return {
        "Word": word,
        "Word Translation": _read(
            os.path.join(folder, "word_translation.txt")),
        "Sentence": sentence,
        "Sentence Translation": _read(
            os.path.join(folder, "sentence_translation.txt")),
        "Screenshot": "",
        "Audio": "",
    }


def _media_refs(folder, card_id, fields):
    """Point the fields at per-card media names ("card_0001_screenshot.png"
    -- deterministic, collision-free) and return the files to upload."""
    uploads = []
    for src_name, field, wrap in (
        ("screenshot.png", "Screenshot", '<img src="%s">'),
        ("audio.wav", "Audio", "[sound:%s]"),
    ):
        src = os.path.join(folder, src_name)
        if os.path.isfile(src):
            name = "%s_%s" % (card_id, src_name)
            fields[field] = wrap % name
            uploads.append((name, src))
    return uploads


# ---------------------------------------------------- the open route
def _http_request(payload, timeout):
    req = urllib.request.Request(
        URL, payload, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# Tests swap this for a fake Anki; everything above the socket runs as-is.
_transport = _http_request


def _invoke(action, timeout=_TIMEOUT, **params):
    payload = json.dumps(
        {"action": action, "version": 6, "params": params}).encode("utf-8")
    try:
        raw = _transport(payload, timeout)
    except OSError as exc:
        raise SyncError("AnkiConnect didn't answer: %s" % exc) from exc
    reply = json.loads(raw)
    if reply.get("error"):
        raise SyncError("AnkiConnect: %s" % reply["error"])
    return reply.get("result")


def available():
    """True only when a running Anki answers on the AnkiConnect port. The
    probe sits on the card-save thread, hence the short fuse."""
    try:
        _invoke("version", timeout=_PROBE_TIMEOUT)
        return True
    except Exception:
        return False


def _sync_live(folders, deck):
    _invoke("createDeck", deck=deck)   # Anki's own get-or-create by name
    if NOTETYPE not in _invoke("modelNames"):
        t = prefs.template()
        _invoke("createModel", modelName=NOTETYPE,
                inOrderFields=FIELD_NAMES, css=t.get("css", ""),
                cardTemplates=[{"Name": "Cappa", "Front": t.get("front", ""),
                                "Back": t.get("back", "")}])
    added = 0
    for folder in folders:
        card_id = os.path.basename(folder)
        try:
            fields = _card_fields(folder)
            if fields is None:
                continue
            uploads = _media_refs(folder, card_id, fields)
            tag = "cappa::%s" % card_id
            if _invoke("findNotes", query="tag:%s" % tag):
                _mark_delivered(folder, "adoption (already in Anki)")
                continue
            for name, path in uploads:
                with open(path, "rb") as f:
                    data = base64.b64encode(f.read()).decode("ascii")
                _invoke("storeMediaFile", filename=name, data=data)
            # allowDuplicate: the same word seen twice is two cards here.
            _invoke("addNote", note={
                "deckName": deck, "modelName": NOTETYPE, "fields": fields,
                "options": {"allowDuplicate": True}, "tags": [tag]})
            _mark_delivered(folder, "a running Anki (AnkiConnect)")
            added += 1
        except Exception as exc:
            # This card stays receipt-less and rides the next save.
            print("[cappa] anki sync: %s skipped: %s" % (card_id, exc))
    return added


# -------------------------------------------------- the closed route
def _find_collection_path():
    """The default profile's collection.anki2 under %APPDATA%\\Anki2; with
    several profiles the most recently used wins."""
    base = os.path.join(os.environ.get("APPDATA", ""), "Anki2")
    if not os.path.isdir(base):
        return None
    candidates = [os.path.join(base, name, "collection.anki2")
                  for name in os.listdir(base)]
    candidates = [p for p in candidates if os.path.isfile(p)]
    return max(candidates, key=os.path.getmtime) if candidates else None


def _backup(path):
    """A rotating pre-write safety copy. Never blocks the sync on failure
    -- it's insurance, not a requirement."""
    try:
        folder = os.path.dirname(path)
        prefix = os.path.basename(path) + ".cappa-backup-"
        backups = sorted(f for f in os.listdir(folder) if f.startswith(prefix))
        while len(backups) >= _MAX_BACKUPS:
            os.remove(os.path.join(folder, backups.pop(0)))
        shutil.copyfile(path, os.path.join(
            folder, prefix + time.strftime("%Y%m%d-%H%M%S")))
    except OSError as exc:
        print("[cappa] anki backup skipped:", exc)


def _col_notetype(col):
    nt = col.models.by_name(NOTETYPE)
    if nt is not None:
        return nt
    t = prefs.template()
    nt = col.models.new(NOTETYPE)
    for fname in FIELD_NAMES:
        col.models.add_field(nt, col.models.new_field(fname))
    tmpl = col.models.new_template("Cappa")
    tmpl["qfmt"], tmpl["afmt"] = t.get("front", ""), t.get("back", "")
    col.models.add_template(nt, tmpl)
    nt["css"] = t.get("css", "")
    col.models.add_dict(nt)
    return col.models.by_name(NOTETYPE)


def _sync_closed(folders, deck, collection_path):
    try:
        from anki.collection import Collection
        from anki.errors import DBError
    except ImportError as exc:
        raise SyncError(
            "The 'anki' package isn't installed (pip install anki)") from exc

    path = collection_path or _find_collection_path()
    if not path:
        raise SyncError(
            "Couldn't find an Anki profile "
            "(no collection.anki2 under %APPDATA%\\Anki2)")

    _backup(path)
    try:
        col = Collection(path)
    except DBError as exc:
        # Only reachable when the AnkiConnect probe already failed: the
        # app is open but the add-on isn't answering.
        raise SyncError(
            "Anki is open but AnkiConnect isn't answering — enable the "
            "add-on (code 2055492159), or close Anki") from exc
    except Exception as exc:
        raise SyncError("Couldn't open your Anki collection: %s" % exc) from exc

    added = 0
    try:
        deck_id = col.decks.add_normal_deck_with_name(deck).id
        for folder in folders:
            card_id = os.path.basename(folder)
            try:
                fields = _card_fields(folder)
                if fields is None:
                    continue
                uploads = _media_refs(folder, card_id, fields)
                tag = "cappa::%s" % card_id
                if col.find_notes("tag:%s" % tag):
                    _mark_delivered(folder, "adoption (already in Anki)")
                    continue
                # add_file names media after its source file, so stage
                # each upload under its per-card name first.
                stage = tempfile.mkdtemp(prefix="cappa_anki_")
                try:
                    for name, src in uploads:
                        staged = os.path.join(stage, name)
                        shutil.copyfile(src, staged)
                        col.media.add_file(staged)
                finally:
                    shutil.rmtree(stage, ignore_errors=True)
                note = col.new_note(_col_notetype(col))
                for fname in FIELD_NAMES:
                    note[fname] = fields[fname]
                note.add_tag(tag)
                col.add_note(note, deck_id)
                _mark_delivered(folder, "the collection file")
                added += 1
            except Exception as exc:
                print("[cappa] anki sync: %s skipped: %s" % (card_id, exc))
    finally:
        col.close()
    return added


# ---------------------------------------------------------------- sync
def sync(cards_dir=None, source_language=None, collection_path=None):
    """Deliver every receipt-less card -- normally exactly the one just
    saved, plus any earlier save Anki wasn't reachable for. Returns how
    many were added; raises SyncError (message fit to show the user) when
    Anki couldn't be reached at all. One card's own problem never aborts
    the batch."""
    from .builder import CARDS_DIR
    folders = _pending_dirs(cards_dir or CARDS_DIR)
    if not folders:
        return 0   # nothing new -- don't even touch Anki
    deck = deck_name(source_language)
    if available():
        return _sync_live(folders, deck)
    return _sync_closed(folders, deck, collection_path)
