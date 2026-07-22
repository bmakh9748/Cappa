"""Unit test for cappa/flashcard/anki_sync.py -- per-card delivery into
Anki. Both routes are exercised without touching the user's real Anki:
the OPEN route against a fake AnkiConnect at the module's transport seam
(a callable eating the exact JSON the add-on would receive), the CLOSED
route against a throwaway collection file via the real `anki` package.
No network either way.
"""

import base64
import json
import os
import sys
import tempfile
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from anki.collection import Collection

import cappa.flashcard as flashcard
from cappa.flashcard import anki_sync


def _write_text(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _write_wav(path, byte):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(1000)
        w.writeframes(bytes([byte, 0]) * 100)


def _make_card(cards_dir, name, word, sentence, with_media=False):
    folder = os.path.join(cards_dir, name)
    os.makedirs(folder, exist_ok=True)
    _write_text(os.path.join(folder, "word.txt"), word)
    _write_text(os.path.join(folder, "sentence.txt"), sentence)
    _write_text(os.path.join(folder, "word_translation.txt"), "tx:" + word)
    _write_text(os.path.join(folder, "sentence_translation.txt"),
                "tx:" + sentence)
    if with_media:
        with open(os.path.join(folder, "screenshot.png"), "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + name.encode())
        _write_wav(os.path.join(folder, "audio.wav"), len(name))
    return folder


def _receipt(cards_dir, name):
    return os.path.join(cards_dir, name, anki_sync.MARKER)


def _refused(payload, timeout):
    raise ConnectionRefusedError("refused")


class FakeAnki:
    """Just enough AnkiConnect to satisfy anki_sync, with state to assert
    on afterwards."""

    def __init__(self):
        self.decks = set()
        self.models = set()
        self.model_fields = {}   # modelName -> [field names], for migration
        self.notes = []      # {"deck", "model", "fields", "tags"}
        self.media = {}      # filename -> bytes

    def __call__(self, payload, timeout):
        req = json.loads(payload.decode("utf-8"))
        assert req["version"] == 6
        result = getattr(self, "_" + req["action"])(req.get("params", {}))
        return json.dumps({"result": result, "error": None}).encode("utf-8")

    def _version(self, p):
        return 6

    def _createDeck(self, p):
        self.decks.add(p["deck"])
        return 1

    def _modelNames(self, p):
        return sorted(self.models)

    def _createModel(self, p):
        assert p["inOrderFields"] == anki_sync.FIELD_NAMES
        self.models.add(p["modelName"])
        self.model_fields[p["modelName"]] = list(p["inOrderFields"])
        return {}

    def _modelFieldNames(self, p):
        return list(self.model_fields.get(p["modelName"], []))

    def _modelFieldAdd(self, p):
        self.model_fields.setdefault(p["modelName"], []).append(p["fieldName"])
        return None

    def _updateModelTemplates(self, p):
        return None

    def _updateModelStyling(self, p):
        return None

    def _findNotes(self, p):
        assert p["query"].startswith("tag:")
        tag = p["query"][4:]
        return [i for i, n in enumerate(self.notes) if tag in n["tags"]]

    def _addNote(self, p):
        note = p["note"]
        assert note["modelName"] in self.models
        assert note["deckName"] in self.decks
        self.notes.append({"deck": note["deckName"],
                           "model": note["modelName"],
                           "fields": dict(note["fields"]),
                           "tags": list(note["tags"])})
        return len(self.notes)

    def _storeMediaFile(self, p):
        self.media[p["filename"]] = base64.b64decode(p["data"])
        return p["filename"]


# deck_name: the learning language names the deck; "auto" stays plain.
assert anki_sync.deck_name("ja") == "Cappa Japanese"
assert anki_sync.deck_name("auto") == "Cappa"
assert anki_sync.deck_name("not-a-real-code") == "Cappa"
print("PASS: deck_name follows the learning language, 'auto' stays plain")

# _find_collection_path: the most-recently-used profile under a fake
# %APPDATA%\Anki2 wins -- never touches the real one.
with tempfile.TemporaryDirectory() as fake_appdata:
    older = os.path.join(fake_appdata, "Anki2", "User 1", "collection.anki2")
    newer = os.path.join(fake_appdata, "Anki2", "User 2", "collection.anki2")
    for path, stamp in ((older, 1000), (newer, 2000)):
        os.makedirs(os.path.dirname(path))
        open(path, "wb").close()
        os.utime(path, (stamp, stamp))
    old_appdata = os.environ.get("APPDATA")
    os.environ["APPDATA"] = fake_appdata
    try:
        assert anki_sync._find_collection_path() == newer
    finally:
        os.environ["APPDATA"] = old_appdata
print("PASS: _find_collection_path picks the most recently used profile")

# available(): a dead port means no, an answering fake means yes.
old_transport = anki_sync._transport
try:
    anki_sync._transport = _refused
    assert anki_sync.available() is False
    anki_sync._transport = FakeAnki()
    assert anki_sync.available() is True
finally:
    anki_sync._transport = old_transport
print("PASS: available() reads a dead port as closed, an answer as open")

# ---------------- the CLOSED route (probe refused -> collection file) ----
try:
    anki_sync._transport = _refused
    with tempfile.TemporaryDirectory() as cards_dir, \
         tempfile.TemporaryDirectory() as anki_dir:
        col_path = os.path.join(anki_dir, "collection.anki2")

        # No cards at all: never even touches Anki.
        assert anki_sync.sync(cards_dir=cards_dir, source_language="ja",
                              collection_path=col_path) == 0
        assert not os.path.exists(col_path)
        print("PASS: an empty cards folder never touches Anki")

        _make_card(cards_dir, "card_0001", "witaj", "witaj swiecie",
                   with_media=True)
        _make_card(cards_dir, "card_0002", "dzien dobry", "dzien dobry pani")
        _make_card(cards_dir, "card_0003", "", "")   # failed draft: skip

        added = anki_sync.sync(cards_dir=cards_dir, source_language="ja",
                               collection_path=col_path)
        assert added == 2, added
        assert os.path.isfile(_receipt(cards_dir, "card_0001"))
        assert os.path.isfile(_receipt(cards_dir, "card_0002"))
        assert not os.path.exists(_receipt(cards_dir, "card_0003"))

        col = Collection(col_path)
        try:
            deck_names = [d.name for d in col.decks.all_names_and_ids()]
            assert "Cappa Japanese" in deck_names, deck_names
            ids = col.find_notes("tag:cappa::card_0001")
            assert len(ids) == 1, ids
            n1 = col.get_note(ids[0])
            assert n1["Word"] == "witaj"
            assert n1["Sentence"] == "witaj swiecie"
            assert n1["Screenshot"] == '<img src="card_0001_screenshot.png">'
            assert n1["Audio"] == "[sound:card_0001_audio.wav]"
            n2 = col.get_note(col.find_notes("tag:cappa::card_0002")[0])
            assert n2["Screenshot"] == "" and n2["Audio"] == ""
        finally:
            col.close()
        print("PASS: closed route creates the deck, writes fields + tagged "
              "media, receipts delivered cards, skips empty drafts")

        # Re-run: receipts mean nothing is re-sent, nothing duplicated --
        # and a delivered card edited on disk is left alone in Anki.
        _write_text(os.path.join(cards_dir, "card_0001", "word.txt"),
                    "WITAJ!!")
        assert anki_sync.sync(cards_dir=cards_dir, source_language="ja",
                              collection_path=col_path) == 0
        col = Collection(col_path)
        try:
            ids = col.find_notes("tag:cappa::card_0001")
            assert len(ids) == 1
            assert col.get_note(ids[0])["Word"] == "witaj"   # untouched
        finally:
            col.close()
        print("PASS: a delivered card is never re-sent, even edited on disk")

        # A NEW card syncs alone; a lost receipt is recovered by adoption
        # (found by tag -> receipt back, no duplicate).
        _make_card(cards_dir, "card_0004", "nowy", "nowy zwrot")
        os.remove(_receipt(cards_dir, "card_0002"))
        assert anki_sync.sync(cards_dir=cards_dir, source_language="ja",
                              collection_path=col_path) == 1
        assert os.path.isfile(_receipt(cards_dir, "card_0004"))
        assert os.path.isfile(_receipt(cards_dir, "card_0002"))   # adopted
        col = Collection(col_path)
        try:
            assert len(col.find_notes("tag:cappa::*")) == 3
            assert len(col.find_notes("tag:cappa::card_0002")) == 1
        finally:
            col.close()
        print("PASS: only the new card syncs; a lost receipt is adopted "
              "back without duplicating")

        # The notetype tracks the design: turn Breakdown on, sync a new card,
        # and the card template must gain {{Breakdown}} so a field switched on
        # in settings actually renders (fields added, faces refreshed).
        import cappa.flashcard.prefs as prefs
        prefs.set_card_fields({"breakdown": "back"})
        try:
            _make_card(cards_dir, "card_0005", "hej", "hej tam")
            assert anki_sync.sync(cards_dir=cards_dir, source_language="ja",
                                  collection_path=col_path) == 1
            col = Collection(col_path)
            try:
                nt = col.models.by_name(anki_sync.NOTETYPE)
                names = {f["name"] for f in nt["flds"]}
                assert {"Breakdown", "Word Audio"} <= names, names
                assert "{{Breakdown}}" in nt["tmpls"][0]["afmt"], (
                    nt["tmpls"][0]["afmt"])
            finally:
                col.close()
            print("PASS: the notetype tracks the design — a field turned on "
                  "gains its template slot")
        finally:
            prefs.set_card_fields(None)
finally:
    anki_sync._transport = old_transport

# ---------------- the OPEN route (fake add-on), via the dispatcher -------
fake = FakeAnki()
try:
    anki_sync._transport = fake
    with tempfile.TemporaryDirectory() as cards_dir:
        _make_card(cards_dir, "card_0001", "witaj", "witaj swiecie",
                   with_media=True)
        _make_card(cards_dir, "card_0002", "dzien dobry", "dzien dobry pani")

        # flashcard.sync_to_anki is the exact call the button makes; an
        # answering port must route it to the live path.
        added = flashcard.sync_to_anki(cards_dir=cards_dir,
                                       source_language="ja")
        assert added == 2, added
        assert "Cappa Japanese" in fake.decks
        n1 = [n for n in fake.notes if "cappa::card_0001" in n["tags"]][0]
        assert n1["fields"]["Word"] == "witaj"
        assert n1["fields"]["Screenshot"] == (
            '<img src="card_0001_screenshot.png">')
        assert n1["fields"]["Audio"] == "[sound:card_0001_audio.wav]"
        assert fake.media["card_0001_screenshot.png"].startswith(b"\x89PNG")
        assert fake.media["card_0001_audio.wav"].startswith(b"RIFF")
        assert os.path.isfile(_receipt(cards_dir, "card_0001"))
        print("PASS: open route lands cards live -- deck, fields, media, "
              "receipts")

        # Re-run: nothing re-sent; a lost receipt is adopted, not re-added.
        os.remove(_receipt(cards_dir, "card_0001"))
        media_before = dict(fake.media)
        assert flashcard.sync_to_anki(cards_dir=cards_dir,
                                      source_language="ja") == 0
        assert len(fake.notes) == 2
        assert fake.media == media_before
        assert os.path.isfile(_receipt(cards_dir, "card_0001"))
        print("PASS: live re-run re-sends nothing and adopts a lost receipt")

        # Migration: a notetype from before a field existed (only the first
        # six) gains Breakdown + Word Audio on the next sync, so the new
        # pieces can render on old and new cards alike.
        fake.model_fields[anki_sync.NOTETYPE] = anki_sync.FIELD_NAMES[:6]
        _make_card(cards_dir, "card_0003", "czesc", "czesc wam")
        assert flashcard.sync_to_anki(cards_dir=cards_dir,
                                      source_language="ja") == 1
        assert "Breakdown" in fake.model_fields[anki_sync.NOTETYPE]
        assert "Word Audio" in fake.model_fields[anki_sync.NOTETYPE]
        print("PASS: an existing notetype gains new fields on the next sync")
finally:
    anki_sync._transport = old_transport
