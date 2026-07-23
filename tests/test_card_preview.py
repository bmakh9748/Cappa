"""Unit test for the card preview and the discard path behind it.

No window is shown: the preview is built and its body repainted in place, so
this runs with the other instant tests. What matters here is the contract the
preview owes anki_sync -- sync() delivers every card_ folder without a
receipt, so a rejected draft MUST leave the disk, and a delivered one must
never be deleted out from under Anki.
"""

import os
import sys
import tempfile
import threading
import time
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from cappa.flashcard import discard_draft
from cappa.flashcard.anki_sync import MARKER
from cappa.flashcard.model import CardDraft
from cappa.ui.card_preview import CardPreview

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
       b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
       b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")


def make_draft(cards_dir, name="card_0001"):
    """A finished draft on disk, the way build_draft leaves one."""
    folder = os.path.join(cards_dir, name)
    os.makedirs(folder, exist_ok=True)
    draft = CardDraft("kucing", "tadi kucing saya melahirkan")
    draft.word_translation = "cat"
    draft.sentence_translation = "my cat just gave birth"
    draft.folder_path = folder
    draft.image_path = os.path.join(folder, "screenshot.png")
    with open(draft.image_path, "wb") as f:
        f.write(PNG)
    draft.audio_path = os.path.join(folder, "audio.wav")
    with wave.open(draft.audio_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(1000)
        w.writeframes(b"\x00\x00" * 1200)
    draft.audio_seconds = 1.2
    draft.notes.append("audio came from the loopback buffer")
    return draft


app = QApplication.instance() or QApplication([])


def drain(preview):
    """Block until the preview's one edit thread has run every queued job:
    a sentinel is FIFO-behind all real writes, so its completion means the
    folder is fully written (the async edits finish before tmp cleanup)."""
    done = threading.Event()
    preview._jobs.put((preview._ereq, "text", done.set))
    assert done.wait(3.0), "edit queue did not drain"

with tempfile.TemporaryDirectory() as tmp:
    # ---- the preview shows every piece of the card, and its notes --------
    draft = make_draft(tmp)
    preview = CardPreview()
    preview._draft = draft
    preview._rebuild()
    texts = [w.text() for w in preview._body.findChildren(QLabel)]
    buttons = [b.text() for b in preview._body.findChildren(QPushButton)]
    assert "kucing" in texts, texts
    assert "cat" in texts, texts
    assert "tadi kucing saya melahirkan" in texts, texts
    assert "my cat just gave birth" in texts, texts
    assert any(t.startswith("⚠ audio came from") for t in texts), texts
    assert any(b.startswith("▶") and "1.2 s" in b for b in buttons), buttons
    # the screenshot renders as a pixmap, not a "— no screenshot" line
    assert any(w.pixmap() and not w.pixmap().isNull()
               for w in preview._body.findChildren(QLabel)), "no screenshot"
    assert not any(t.startswith("—") for t in texts), texts
    print("PASS: preview shows word, translations, sentence, image, audio, notes")

    # ---- a missing piece is shown as missing, not hidden -----------------
    bare = make_draft(tmp, "card_0002")
    os.remove(bare.audio_path)
    bare.audio_path = None
    preview._draft = bare
    preview._rebuild()
    texts = [w.text() for w in preview._body.findChildren(QLabel)]
    assert "— no audio" in texts, texts
    print("PASS: a dropped audio clip is visible as '— no audio'")

    # ---- Discard deletes the folder: an unreceipted draft would sync -----
    preview._draft = draft
    folder = draft.folder_path
    preview._discard()
    assert not os.path.isdir(folder), "discarded draft still on disk"
    assert draft.folder_path is None
    assert preview._draft is None
    print("PASS: Discard deletes the draft folder")

    # ---- a delivered card is never deleted: Anki's copy is the user's ----
    delivered = make_draft(tmp, "card_0003")
    with open(os.path.join(delivered.folder_path, MARKER), "w") as f:
        f.write("synced\n")
    assert discard_draft(delivered) is False
    assert os.path.isdir(delivered.folder_path), "deleted a card Anki holds"
    print("PASS: a receipted card survives discard_draft")

    # ---- superseding drafts are discarded, never abandoned ---------------
    first = make_draft(tmp, "card_0004")
    second = make_draft(tmp, "card_0005")
    first_folder = first.folder_path
    preview._draft = first
    preview._discard_current("superseded")
    preview._draft = second
    assert not os.path.isdir(first_folder), "abandoned draft would resync"
    print("PASS: a superseded draft is discarded, not left to resync")

    # ---- a folder that isn't a card draft is refused ---------------------
    stray = CardDraft("x", "y")
    stray.folder_path = os.path.join(tmp, "transcripts")
    os.makedirs(stray.folder_path, exist_ok=True)
    assert discard_draft(stray) is False
    assert os.path.isdir(stray.folder_path)
    print("PASS: discard_draft refuses a folder that isn't a card_ draft")

    # ---- editable fields carry a ✎; a manual sentence edit detaches -------
    edit_draft = make_draft(tmp, "card_0006")
    preview._draft = edit_draft
    preview._detached = False
    preview._rebuild()
    pencils = [b for b in preview._body.findChildren(QPushButton)
               if b.text() == "✎"]
    assert len(pencils) == 4, pencils   # word, sentence, both translations
    preview._start_edit("sentence")
    from PySide6.QtWidgets import QPlainTextEdit
    box = preview._editing["sentence"][0]
    assert isinstance(box, QPlainTextEdit)
    box.setPlainText("a hand typed sentence")
    preview._commit_edit("sentence")
    assert preview._detached, "a typed sentence must detach the slider"
    drain(preview)                      # let the async write land
    assert edit_draft.sentence == "a hand typed sentence"
    assert (edit_draft.edited or {}).get("manual") == ["sentence"]
    on_disk = open(os.path.join(edit_draft.folder_path, "sentence.txt"),
                   encoding="utf-8").read()
    assert on_disk == "a hand typed sentence", on_disk
    print("PASS: editable fields have a ✎; a typed sentence detaches the slider")

    # ---- Add waits for in-flight edits to drain before syncing -----------
    import cappa.flashcard as _fc
    real_sync = _fc.sync_to_anki
    synced = []
    _fc.sync_to_anki = lambda: synced.append(True) or False
    try:
        drain = make_draft(tmp, "card_0007")
        preview._draft = drain
        preview._busy = 1               # an edit is 'in flight'
        preview._pending_add = False
        preview._add_to_anki()
        assert preview._pending_add, "Add should wait, not sync mid-edit"
        assert not synced, "sync must not start while an edit is pending"
        # the edit lands: draining to zero releases the deferred Add
        preview._edit_done(preview._ereq, "text", None, "")
        deadline = time.monotonic() + 3.0
        while not synced and time.monotonic() < deadline:
            time.sleep(0.02)
        assert synced, "the deferred Add never fired after edits drained"
        assert not preview._pending_add
    finally:
        _fc.sync_to_anki = real_sync
    print("PASS: Add defers until edits drain, then delivers what's on disk")

print("\nALL PASS")
