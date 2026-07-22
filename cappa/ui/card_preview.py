"""The card as it will be, shown before it goes to Anki.

Create Anki card gathers the draft and then STOPS here. This window shows
every piece the card will carry -- word, both translations, the sentence,
the click-time screenshot, the audio clip -- grouped onto the front and back
faces the Flashcards settings put them on, plus the draft's notes (the
degradations the pipeline recorded). The pipeline's mistakes -- a wrong OCR
read, a sentence polluted by on-screen furniture, a clip that landed off the
word -- become visible before they reach Anki instead of after. Nothing is
editable yet: the two exits are Add to Anki and Discard.

DISCARD DELETES THE DRAFT FOLDER, and must: an unreceipted draft rides the
next save into Anki (see flashcard/writer.discard_draft).

A top-level window, not an overlay child like the word popup: the tracked
region can be far smaller than the preview (a narrow Select area) and the
screenshot needs room. Like the overlay and the launcher it is excluded from
capture, so detection can never read our own UI; the overlay counts its hwnd
as "ours" (roots()) so opening it doesn't park the tracking border."""

import os
import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QPushButton,
                               QVBoxLayout, QWidget)

from .. import flashcard, winapi
from ..settings import CARD_BACK, CARD_FIELDS, CARD_FRONT

try:
    import winsound          # stdlib on Windows, and the app is Windows-only
except ImportError:          # keep the preview usable if it ever isn't
    winsound = None

SHOT_WIDTH = 380             # the screenshot scales to this, aspect kept
MAX_TEXT_WIDTH = 400         # long sentences wrap instead of widening

_LABELS = {key: label for key, label, _ in CARD_FIELDS}

_STYLE = """
    #cardPreview {
        background: rgba(18, 20, 28, 245);
        border: 1px solid rgba(255, 255, 255, 34);
        border-radius: 12px;
    }
    QLabel#title { color: #eaeaf0; font-size: 15px; font-weight: bold; }
    QLabel#face {
        color: #7f8496; font-size: 11px; font-weight: bold;
        letter-spacing: 1px;
    }
    QLabel#fieldName { color: #8a8fa2; font-size: 11px; }
    QLabel#fieldValue { color: #eaeaf0; font-size: 14px; }
    QLabel#missing { color: rgba(199, 201, 212, 110); font-size: 13px; }
    QLabel#note { color: #e8c98a; font-size: 12px; }
    QLabel#status { color: #c6cad8; font-size: 12px; }
    #rule { background: rgba(255, 255, 255, 30); }
    QPushButton#add {
        color: #bfe9ff; background: rgba(90, 210, 255, 30);
        border: 1px solid rgba(90, 210, 255, 95);
        border-radius: 6px; padding: 6px 14px;
        font-size: 12px; font-weight: bold;
    }
    QPushButton#add:hover { background: rgba(90, 210, 255, 64); }
    QPushButton#add:disabled {
        color: rgba(199, 201, 212, 110);
        background: rgba(255, 255, 255, 12);
        border-color: rgba(255, 255, 255, 26);
    }
    QPushButton#plain {
        color: #c7c9d4; background: rgba(255, 255, 255, 20);
        border: none; border-radius: 6px; padding: 6px 12px; font-size: 12px;
    }
    QPushButton#plain:hover { background: rgba(255, 255, 255, 40); }
    QPushButton#discard {
        color: #ffbdbd; background: rgba(226, 76, 76, 34);
        border: 1px solid rgba(226, 76, 76, 110);
        border-radius: 6px; padding: 6px 12px; font-size: 12px;
    }
    QPushButton#discard:hover { background: rgba(226, 76, 76, 150);
                                color: #ffffff; }
    QPushButton#play {
        color: #c7c9d4; background: rgba(255, 255, 255, 20);
        border: none; border-radius: 6px; padding: 4px 10px; font-size: 12px;
    }
    QPushButton#play:hover { background: rgba(255, 255, 255, 40); }
"""


class CardPreview(QWidget):
    # (request id, ok, message) from the sync thread; Qt queues it onto the
    # UI thread because the emitter is a foreign thread.
    _synced = Signal(int, bool, str)

    def __init__(self):
        super().__init__(None)
        self.setObjectName("cardPreview")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint
                            | Qt.WindowStaysOnTopHint)
        self.setWindowTitle("Card preview")
        self._draft = None
        self._req = 0          # stale-response guard, like the word popup's

        title = QLabel("Card preview", self)
        title.setObjectName("title")
        # Closing IS discarding: an unresolved draft on disk gets swept into
        # Anki by the next save, so there is no "leave it for later" exit.
        close = QPushButton("✕", self)
        close.setObjectName("plain")
        close.setCursor(Qt.PointingHandCursor)
        close.setToolTip("Discard this card")
        close.clicked.connect(self._discard)
        head = QHBoxLayout()
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(close)

        self._body = QWidget(self)
        self._body_lay = QVBoxLayout(self._body)
        self._body_lay.setContentsMargins(0, 0, 0, 0)
        self._body_lay.setSpacing(12)

        self._status = QLabel("", self)
        self._status.setObjectName("status")
        self._status.setWordWrap(True)
        self._status.setMaximumWidth(MAX_TEXT_WIDTH)

        self._add = QPushButton("Add to Anki", self)
        self._add.setObjectName("add")
        self._add.setCursor(Qt.PointingHandCursor)
        self._add.clicked.connect(self._add_to_anki)
        self._discard_btn = QPushButton("Discard", self)
        self._discard_btn.setObjectName("discard")
        self._discard_btn.setCursor(Qt.PointingHandCursor)
        self._discard_btn.clicked.connect(self._discard)
        self._close_btn = QPushButton("Close", self)
        self._close_btn.setObjectName("plain")
        self._close_btn.setCursor(Qt.PointingHandCursor)
        self._close_btn.clicked.connect(self._keep_and_close)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addWidget(self._add)
        buttons.addWidget(self._discard_btn)
        buttons.addWidget(self._close_btn)
        buttons.addStretch(1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 16)
        lay.setSpacing(12)
        lay.addLayout(head)
        lay.addWidget(self._body)
        lay.addWidget(self._status)
        lay.addLayout(buttons)
        self.setStyleSheet(_STYLE)
        self._synced.connect(self._sync_done)
        # winId() forces the native window into existence so it can be kept
        # out of captured frames, exactly as the launcher does.
        winapi.exclude_from_capture(int(self.winId()))
        self.hide()

    def roots(self):
        """Our top-level hwnd while open -- what the overlay counts as 'ours'
        when it decides whether the tracked window lost the foreground."""
        return (int(self.winId()),) if self.isVisible() else ()

    def show_draft(self, draft):
        """Show what this draft would put on the card. A draft still sitting
        here unresolved is discarded first: the user has moved to another
        word, and an abandoned folder would sync itself later."""
        if self._draft is not None and self._draft is not draft:
            self._discard_current("superseded")
        self._draft = draft
        self._req += 1
        self._rebuild()
        self._status.setText("")
        self._add.setVisible(True)
        self._add.setEnabled(True)
        self._discard_btn.setVisible(True)
        self._discard_btn.setEnabled(True)
        self._close_btn.setVisible(False)
        self.adjustSize()
        self._center()
        self.show()
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------- contents
    def _rebuild(self):
        """Repaint the body for the current draft: the fields on each face,
        in the order the card shows them, then the draft's notes."""
        while self._body_lay.count():
            item = self._body_lay.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        layout = flashcard.prefs.layout()
        for side, heading in ((CARD_FRONT, "FRONT"), (CARD_BACK, "BACK")):
            keys = layout.get(side) or []
            if not keys:
                continue
            self._body_lay.addWidget(self._face(heading))
            for key in keys:
                self._body_lay.addWidget(self._field(key))
        for note in self._draft.notes:
            label = QLabel("⚠ " + note, self._body)
            label.setObjectName("note")
            label.setWordWrap(True)
            label.setMaximumWidth(MAX_TEXT_WIDTH)
            self._body_lay.addWidget(label)

    def _face(self, heading):
        holder = QWidget(self._body)
        rule = QWidget(holder)
        rule.setObjectName("rule")
        rule.setAttribute(Qt.WA_StyledBackground, True)
        rule.setFixedHeight(1)
        label = QLabel(heading, holder)
        label.setObjectName("face")
        row = QVBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(label)
        row.addWidget(rule)
        return holder

    def _field(self, key):
        holder = QWidget(self._body)
        name = QLabel(_LABELS.get(key, key), holder)
        name.setObjectName("fieldName")
        col = QVBoxLayout(holder)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)
        col.addWidget(name)
        value = self._value(key, holder)
        # The play button hugs its label; text and the screenshot take the
        # full width so long sentences wrap rather than widen the window.
        if isinstance(value, QPushButton):
            col.addWidget(value, alignment=Qt.AlignLeft)
        else:
            col.addWidget(value)
        return holder

    def _value(self, key, holder):
        """The widget showing one field, or a dim line saying it is empty --
        an absent screenshot or a clip dropped for silence is exactly what
        the user came here to see."""
        draft = self._draft
        if key == "screenshot":
            return self._image(holder, draft.image_path)
        if key == "audio":
            return self._audio(holder, draft.audio_path, draft.audio_seconds)
        if key == "word_audio":
            return self._word_audio(holder, draft.word_audio_path)
        if key == "breakdown":
            return self._breakdown(holder, draft.breakdown)
        text = {
            "word": draft.word,
            "word_translation": draft.word_translation,
            "sentence": draft.sentence,
            "sentence_translation": draft.sentence_translation,
        }.get(key, "")
        if not text:
            return _missing(holder, "— empty")
        label = QLabel(text, holder)
        label.setObjectName("fieldValue")
        label.setWordWrap(True)
        label.setMaximumWidth(MAX_TEXT_WIDTH)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        return label

    def _breakdown(self, holder, html_text):
        """The word's anatomy as rich text -- the same markup the popup's
        Grammar tab and the card show."""
        if not html_text:
            return _missing(holder, "— empty")
        label = QLabel(holder)
        label.setObjectName("fieldValue")
        label.setTextFormat(Qt.RichText)
        label.setText(html_text)
        label.setWordWrap(True)
        label.setMaximumWidth(MAX_TEXT_WIDTH)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        return label

    def _word_audio(self, holder, path):
        if not path or not os.path.isfile(path):
            return _missing(holder, "— no word audio")
        button = QPushButton("▶  Say the word", holder)
        button.setObjectName("play")
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(lambda: _play_mp3(path))
        return button

    def _image(self, holder, path):
        if not path or not os.path.isfile(path):
            return _missing(holder, "— no screenshot")
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return _missing(holder, "— screenshot unreadable")
        label = QLabel(holder)
        label.setPixmap(pixmap.scaledToWidth(
            min(SHOT_WIDTH, pixmap.width()), Qt.SmoothTransformation))
        return label

    def _audio(self, holder, path, seconds):
        if not path or not os.path.isfile(path):
            return _missing(holder, "— no audio")
        button = QPushButton("▶  Play  ·  %.1f s" % (seconds or 0.0), holder)
        button.setObjectName("play")
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(lambda: _play(path))
        return button

    # -------------------------------------------------------------- actions
    def _add_to_anki(self):
        if self._draft is None:
            return
        self._add.setEnabled(False)
        self._discard_btn.setEnabled(False)
        self._status.setText("Adding to Anki…")
        self._req += 1
        threading.Thread(target=self._sync, args=(self._req,),
                         daemon=True).start()

    def _sync(self, req):
        """Helper thread: deliver the draft -- live into the open app, or into
        its collection file when Anki is closed. Never raises."""
        try:
            added = flashcard.sync_to_anki()
            ok = True
            message = "Added to Anki" if added else "Anki: nothing new"
        except flashcard.SyncError as exc:
            ok, message = False, "Anki: %s" % exc
        except Exception as exc:
            ok, message = False, "Anki sync failed: %s" % exc
        print("[cappa] anki sync:", message)
        self._synced.emit(req, ok, message)

    def _sync_done(self, req, ok, message):
        if req != self._req:
            return
        self._status.setText(message if ok else "⚠ " + message)
        self._add.setVisible(False)
        # A failed delivery left no receipt, so the draft is still on disk and
        # will ride the next card's save -- keep Discard reachable to drop it.
        self._discard_btn.setVisible(not ok)
        self._discard_btn.setEnabled(True)
        self._close_btn.setVisible(True)
        if ok:
            self._draft = None   # it is Anki's now; Close must not delete it
        self.adjustSize()

    def _discard(self):
        self._discard_current("discarded")
        self.hide()

    def _discard_current(self, why):
        draft = self._draft
        self._draft = None
        if draft is None:
            return
        name = os.path.basename(draft.folder_path or "") or "draft"
        try:
            gone = flashcard.discard_draft(draft)
        except Exception as exc:
            print("[cappa] discard failed:", exc)
            return
        print("[cappa] card %s: %s" % (why, name if gone else name + " kept"))

    def _keep_and_close(self):
        """Close after a sync attempt. A delivered card is Anki's; an
        undelivered one keeps the documented behaviour and rides the next
        save."""
        self._draft = None
        self.hide()

    def _center(self):
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.center().x() - self.width() // 2,
                  max(screen.top() + 20,
                      screen.center().y() - self.height() // 2))


def _missing(holder, text):
    label = QLabel(text, holder)
    label.setObjectName("missing")
    return label


def _play(path):
    """Play the clip through the default output device. Asynchronous so the
    UI keeps breathing; SND_NODEFAULT keeps a bad wav from beeping."""
    if winsound is None:
        return
    try:
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC
                           | winsound.SND_NODEFAULT)
    except Exception as exc:
        print("[cappa] clip playback failed:", exc)


def _play_mp3(path):
    """Play an MP3 the word-audio field carries. winsound is WAV-only, so this
    goes through winapi's MCI player on a daemon thread (the same call
    pronounce.say uses) -- the UI never blocks for the clip's length."""
    def run():
        try:
            winapi.play_mp3_blocking(path)
        except Exception as exc:
            print("[cappa] word-audio playback failed:", exc)
    threading.Thread(target=run, daemon=True).start()
