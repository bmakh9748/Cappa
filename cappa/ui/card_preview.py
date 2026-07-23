"""The card as it will be, shown before it goes to Anki -- now editable.

Create Anki card gathers the draft and then STOPS here. This window shows
every piece the card will carry -- word, both translations, the sentence,
the click-time screenshot, the audio clip -- grouped onto the front and back
faces the Flashcards settings put them on, plus the draft's notes (the
degradations the pipeline recorded). The pipeline's mistakes become visible
before they reach Anki instead of after -- and fixable:

  * the audio rides an edit strip (ui/clip_editor.py): the clip's
    neighbourhood as a waveform, the clip window draggable over it, the
    editor's word timeline underneath (flashcard/edit.py). While the two
    are LINKED, sliding the audio range regrows the sentence word by word
    from the words the new range covers -- and dragging the sentence's own
    span re-cuts the audio to carry those words. A slider-grown sentence
    re-fetches its translation (debounced, off-thread).
  * every text field has a ✎: type anything. A hand edit of the word or
    sentence UNLINKS the strip (typed words must not be slid away) and
    never touches the translations.

Every committed edit rewrites the draft folder at once (audio.wav, the
.txt sidecars, metadata's added "edited" record), so Add to Anki always
delivers what the window shows. Add is briefly disabled while an edit
worker is in flight -- a half-written folder must never be swept.

DISCARD DELETES THE DRAFT FOLDER, and must: an unreceipted draft rides the
next save into Anki (see flashcard/writer.discard_draft).

A top-level window, not an overlay child like the word popup: the tracked
region can be far smaller than the preview (a narrow Select area) and the
screenshot needs room. Like the overlay and the launcher it is excluded from
capture, so detection can never read our own UI; the overlay counts its hwnd
as "ours" (roots()) so opening it doesn't park the tracking border."""

import os
import queue
import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QVBoxLayout,
                               QWidget)

from .. import flashcard, winapi
from ..flashcard import edit as card_edit
from ..language.translate import TranslationError, clean_word, translate
from ..settings import CARD_BACK, CARD_FIELDS, CARD_FRONT
from .clip_editor import ClipEditor

try:
    import winsound          # stdlib on Windows, and the app is Windows-only
except ImportError:          # keep the preview usable if it ever isn't
    winsound = None

SHOT_WIDTH = 380             # the screenshot scales to this, aspect kept
MAX_TEXT_WIDTH = 400         # long sentences wrap instead of widening
RETRANSLATE_MS = 700         # idle time after a slider-grown sentence before
                             # its translation re-fetches: the free endpoint
                             # must not be hit once per dragged word

_LABELS = {key: label for key, label, _ in CARD_FIELDS}
_EDITABLE = ("word", "word_translation", "sentence", "sentence_translation")

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
    QPushButton#play:checked { background: rgba(90, 210, 255, 40); }
    QPushButton#pencil {
        color: #8a8fa2; background: transparent; border: none;
        padding: 0px 4px; font-size: 12px;
    }
    QPushButton#pencil:hover { color: #eaeaf0; }
    QLineEdit, QPlainTextEdit {
        color: #eaeaf0; background: rgba(255, 255, 255, 14);
        border: 1px solid rgba(90, 210, 255, 90);
        border-radius: 5px; padding: 3px 6px; font-size: 13px;
        selection-background-color: rgba(90, 210, 255, 90);
    }
"""


class CardPreview(QWidget):
    # (request id, ok, message) from the sync thread; Qt queues it onto the
    # UI thread because the emitter is a foreign thread.
    _synced = Signal(int, bool, str)
    # (edit request id, what finished, payload, error) from edit worker
    # threads -- editor prep, re-cuts, sentence commits, retranslation.
    _edited = Signal(int, str, object, str)

    def __init__(self):
        super().__init__(None)
        self.setObjectName("cardPreview")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint
                            | Qt.WindowStaysOnTopHint)
        self.setWindowTitle("Card preview")
        self._draft = None
        self._req = 0          # stale-response guard, like the word popup's
        self._source = None    # SourceSession the draft was built against
        self._recorder = None  # LoopbackRecorder, for loopback-timed clips
        self._editor = None    # flashcard.edit.DraftEditor once prepared
        self._ereq = 0         # edit generation: bumped when the draft
                               # changes/leaves so stale edit workers drop
        self._busy = 0         # edit jobs queued or running (gates Add)
        self._pending_add = False  # Add pressed while edits were draining
        self._detached = False # the sentence was typed by hand: the slider
                               # must no longer regrow or revert it
        self._vals = {}        # field key -> its value widget, for updates
        self._rows = {}        # field key -> (holder, its QVBoxLayout)
        self._editing = {}     # field key -> open inline editor widgets
        self._strip = None     # the ClipEditor, once the editor is ready
        self._tx_timer = QTimer(self)
        self._tx_timer.setSingleShot(True)
        self._tx_timer.timeout.connect(self._retranslate_now)
        # ALL disk-touching edits run on ONE background thread in submission
        # order: overlapping write_artifacts calls would tear the sidecars /
        # metadata, and an out-of-order recut would ship the wrong window.
        # A single FIFO consumer makes both impossible. Daemon, app-lived.
        self._jobs = queue.Queue()
        # Held around each job's CARD-FOLDER write (never its network call) and
        # around a synchronous Discard, so the delete can never race a write:
        # Discard blocks only for the milliseconds a write holds it, then the
        # folder is gone before Discard returns -- so a later sync can't sweep
        # it, and a stale worker afterwards sees folder_path=None and no-ops.
        self._disk_lock = threading.Lock()
        threading.Thread(target=self._job_loop, daemon=True).start()

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
        self._edited.connect(self._edit_done)
        # winId() forces the native window into existence so it can be kept
        # out of captured frames, exactly as the launcher does.
        winapi.exclude_from_capture(int(self.winId()))
        self.hide()

    def roots(self):
        """Our top-level hwnd while open -- what the overlay counts as 'ours'
        when it decides whether the tracked window lost the foreground."""
        return (int(self.winId()),) if self.isVisible() else ()

    def show_draft(self, draft, source=None, recorder=None):
        """Show what this draft would put on the card. A draft still sitting
        here unresolved is discarded first: the user has moved to another
        word, and an abandoned folder would sync itself later. `source` and
        `recorder` are the same pair the draft was built from -- they let
        the edit engine re-cut audio and walk the caption timeline."""
        if self._draft is not None and self._draft is not draft:
            self._discard_current("superseded")
        self._draft = draft
        self._source = source
        self._recorder = recorder
        self._req += 1
        self._ereq += 1
        self._busy = 0
        self._pending_add = False
        self._detached = False
        self._editor = None
        self._tx_timer.stop()
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
        self._prepare_editor()

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
        self._vals = {}
        self._rows = {}
        self._editing = {}
        self._strip = None

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
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(4)
        head.addWidget(name)
        if key in _EDITABLE:
            pencil = QPushButton("✎", holder)
            pencil.setObjectName("pencil")
            pencil.setCursor(Qt.PointingHandCursor)
            pencil.setToolTip("Edit by hand")
            pencil.clicked.connect(lambda _=False, k=key: self._start_edit(k))
            head.addWidget(pencil)
        head.addStretch(1)
        col = QVBoxLayout(holder)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)
        col.addLayout(head)
        value = self._value(key, holder)
        self._vals[key] = value
        self._rows[key] = (holder, col)
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
        label = QLabel(holder)
        label.setWordWrap(True)
        label.setMaximumWidth(MAX_TEXT_WIDTH)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        _set_text(label, text)
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
        button.clicked.connect(self._play_clip)
        return button

    # -------------------------------------------------------- audio editing
    def _prepare_editor(self):
        """Build the edit engine off-thread: it may wait on the audio
        download, and the loopback workspace must be cut before the ring
        rolls past it."""
        draft, source, recorder = self._draft, self._source, self._recorder
        if draft is None or draft.audio_path is None:
            return
        req = self._ereq
        self._work(req, "editor",
                   lambda: card_edit.DraftEditor.prepare(draft, source,
                                                         recorder))

    def _editor_ready(self, editor):
        """Mount the edit strip under the audio row (the strip's own play
        button replaces the plain one)."""
        if editor is None or not editor.ready:
            if editor is not None and editor.error:
                self._status.setText("audio editing unavailable: %s"
                                     % editor.error)
            return
        self._editor = editor
        row = self._rows.get("audio")
        if row is None:
            return
        holder, col = row
        strip = ClipEditor(holder)
        strip.attach(editor, editor.clicked_word_index(self._draft.word))
        strip.rangeEdited.connect(self._on_range)
        strip.spanEdited.connect(self._on_span)
        strip.playClicked.connect(self._play_clip)
        col.addWidget(strip)
        self._strip = strip
        old = self._vals.get("audio")
        if isinstance(old, QPushButton):
            old.hide()
        self.adjustSize()

    def _on_range(self, t0, t1, final):
        """The audio range moved. Linked: the sentence follows the words the
        range now covers -- live on the label while dragging, committed (and
        re-translated) on release."""
        editor, strip = self._editor, self._strip
        if editor is None or strip is None:
            return
        strip.set_duration(t1 - t0)
        span = None
        # A hand-typed sentence (_detached) must never be regrown from the
        # timeline, even re-linked -- the timeline is the pre-edit words.
        if strip.linked() and not self._detached:
            span = editor.words_in_range(t0, t1)
            if span is not None:
                strip.set_span(*span)
                self._preview_sentence(editor.sentence_text(*span))
        if not final:
            return
        self._stop_playback()
        req = self._ereq
        changed_span = span if span is not None \
            and span != tuple(editor.span) else None

        def run():
            with self._disk_lock:
                seconds = editor.recut(t0, t1)
                changed = False
                if changed_span is not None:
                    changed = editor.set_sentence_span(*changed_span)
            return seconds, changed
        self._work(req, "recut", run)

    def _on_span(self, i, j, final):
        """The sentence span moved word by word. Linked: the audio range
        follows the span's spoken window."""
        editor, strip = self._editor, self._strip
        if editor is None or strip is None or self._detached:
            return
        self._preview_sentence(editor.sentence_text(i, j))
        linked = strip.linked()
        if linked:
            t0, t1 = editor.range_for_words(i, j)
            strip.set_selection(*editor.clamp_selection(t0, t1))
        if not final:
            return
        self._stop_playback()
        req = self._ereq

        def run():
            with self._disk_lock:
                changed = editor.set_sentence_span(i, j)
                seconds = None
                if linked:
                    w0, w1 = editor.range_for_words(i, j)
                    seconds = editor.recut(w0, w1)
            return seconds, changed
        self._work(req, "recut", run)

    def _preview_sentence(self, text):
        label = self._vals.get("sentence")
        if isinstance(label, QLabel):
            _set_text(label, text)

    # -------------------------------------------------------- manual edits
    def _start_edit(self, key):
        """Swap the field's label for an inline editor. Committing types the
        text onto the card verbatim; it never moves the sliders and never
        re-translates -- and a typed SENTENCE detaches the strip (unlinks it
        and freezes the sentence span) so automation can't slide the words
        away."""
        if key in self._editing or self._draft is None:
            return
        row = self._rows.get(key)
        if row is None:
            return
        holder, col = row
        current = getattr(self._draft, key)
        if key in ("word", "word_translation"):
            box = QLineEdit(current, holder)
        else:
            box = QPlainTextEdit(current, holder)
            box.setFixedHeight(56)
        box.setMaximumWidth(MAX_TEXT_WIDTH)
        apply_btn = QPushButton("✓ Apply", holder)
        apply_btn.setObjectName("plain")
        apply_btn.setCursor(Qt.PointingHandCursor)
        cancel = QPushButton("✕", holder)
        cancel.setObjectName("plain")
        cancel.setCursor(Qt.PointingHandCursor)
        btns = QWidget(holder)
        btn_lay = QHBoxLayout(btns)
        btn_lay.setContentsMargins(0, 0, 0, 0)
        btn_lay.setSpacing(6)
        btn_lay.addWidget(apply_btn)
        btn_lay.addWidget(cancel)
        btn_lay.addStretch(1)
        col.addWidget(box)
        col.addWidget(btns)
        value = self._vals.get(key)
        if value is not None:
            value.hide()
        self._editing[key] = (box, btns)
        apply_btn.clicked.connect(lambda _=False, k=key: self._commit_edit(k))
        cancel.clicked.connect(lambda _=False, k=key: self._end_edit(k))
        if isinstance(box, QLineEdit):
            box.returnPressed.connect(lambda k=key: self._commit_edit(k))
        box.setFocus()
        self.adjustSize()

    def _end_edit(self, key):
        widgets = self._editing.pop(key, None)
        if widgets is None:
            return
        for w in widgets:
            w.setParent(None)
            w.deleteLater()
        value = self._vals.get(key)
        if value is not None:
            value.show()
        self.adjustSize()

    def _commit_edit(self, key):
        box, _btns = self._editing.get(key, (None, None))
        if box is None or self._draft is None:
            return
        text = box.text() if isinstance(box, QLineEdit) \
            else box.toPlainText()
        text = text.strip()
        if key == "word":
            text = clean_word(text) or text
        self._end_edit(key)
        value = self._vals.get(key)
        if isinstance(value, QLabel):
            _set_text(value, text)
        # A typed sentence no longer matches the word timeline: detach it so
        # neither the audio slider nor the span handle can revert the words.
        if key == "sentence":
            self._detached = True
            if self._strip is not None:
                self._strip.set_linked(False)
                self._strip.lock_span()
        draft = self._draft
        req = self._ereq

        def run():
            with self._disk_lock:
                card_edit.set_text(draft, key, text, manual=True)
        self._work(req, "text", run)

    # ------------------------------------------------------- retranslation
    def _queue_retranslate(self):
        """A slider-grown sentence changes what it means. Debounced: fire
        once the dragging settles, never per word-step."""
        draft = self._draft
        if draft is None or not flashcard.prefs.include("sentence_translation"):
            return
        edited = draft.edited or {}
        if "sentence_translation" in edited.get("manual", ()):
            return   # the user typed this translation; automation keeps off
        label = self._vals.get("sentence_translation")
        if isinstance(label, QLabel):
            _set_text(label, "…")
        self._tx_timer.start(RETRANSLATE_MS)

    def _retranslate_now(self):
        draft = self._draft
        if draft is None:
            return
        req = self._ereq
        sentence = draft.sentence

        def run():
            text = translate(sentence)          # network -- outside the lock
            with self._disk_lock:
                card_edit.set_text(draft, "sentence_translation", text,
                                  manual=False)
            return text
        self._work(req, "translate", run)

    # ------------------------------------------------------- edit plumbing
    def _job_loop(self):
        """The one edit thread: run each queued job to completion, in order,
        before the next starts, reporting back on the _edited signal. Runs for
        the window's whole life (daemon). Each job takes _disk_lock itself for
        its folder writes (see the fns) -- the loop stays lock-free so a slow
        translate fetch never blocks a Discard."""
        while True:
            req, kind, fn = self._jobs.get()
            try:
                self._edited.emit(req, kind, fn(), "")
            except TranslationError as exc:
                self._edited.emit(req, kind, None, str(exc))
            except Exception as exc:
                self._edited.emit(req, kind, None,
                                  "%s failed: %s" % (kind, exc))

    def _work(self, req, kind, fn):
        """Queue one edit step for the single edit thread; the result crosses
        back on the _edited signal. Add to Anki stays disabled while any edit
        is queued or running, so a half-written folder can never be swept."""
        self._busy += 1
        self._add.setEnabled(False)
        self._jobs.put((req, kind, fn))

    def _edit_done(self, req, kind, payload, error):
        if req != self._ereq:
            # A superseded draft's leftovers: the disk write still happened
            # (harmless -- that folder is gone or being replaced), only the
            # UI update is dropped. Its slot in _busy must still be freed, or
            # a queued Add would wait forever.
            self._busy = max(0, self._busy - 1)
            self._maybe_add()
            return
        self._busy = max(0, self._busy - 1)
        if self._busy == 0 and self._close_btn.isHidden() \
                and not self._pending_add:
            self._add.setEnabled(True)
        if error:
            self._status.setText("⚠ " + error)
            if kind == "translate":
                label = self._vals.get("sentence_translation")
                if isinstance(label, QLabel):
                    _set_text(label, self._draft.sentence_translation
                              if self._draft else "")
            return
        if kind == "editor":
            self._editor_ready(payload)
        elif kind == "recut":
            seconds, sentence_changed = payload
            if seconds:
                self._strip.set_duration(seconds)
                button = self._vals.get("audio")
                if isinstance(button, QPushButton):
                    button.setText("▶  Play  ·  %.1f s" % seconds)
            if sentence_changed:
                self._preview_sentence(self._draft.sentence)
                self._queue_retranslate()
        elif kind == "translate":
            label = self._vals.get("sentence_translation")
            if isinstance(label, QLabel):
                _set_text(label, payload)
        self._maybe_add()

    def _maybe_add(self):
        """Start the Anki delivery that was waiting for edits to drain."""
        if self._pending_add and self._busy == 0:
            self._pending_add = False
            self._start_sync()

    # -------------------------------------------------------------- actions
    def _play_clip(self):
        draft = self._draft
        if draft is not None and draft.audio_path:
            _play(draft.audio_path)

    def _stop_playback(self):
        """Let go of audio.wav before a re-cut rewrites it."""
        if winsound is not None:
            try:
                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:
                pass

    def _add_to_anki(self):
        if self._draft is None or self._pending_add:
            return
        # A debounced retranslate still pending would otherwise ship the OLD
        # translation under the grown sentence: flush it into the queue now.
        if self._tx_timer.isActive():
            self._tx_timer.stop()
            self._retranslate_now()
        if self._busy > 0:
            # Wait for every queued edit (incl. the just-flushed translate) to
            # reach disk before sync reads the folder -- _maybe_add fires the
            # delivery once _busy hits 0.
            self._pending_add = True
            self._add.setEnabled(False)
            self._discard_btn.setEnabled(False)
            self._status.setText("Finishing edits…")
            return
        self._start_sync()

    def _start_sync(self):
        self._ereq += 1          # any late edit results are now stale
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
            self._editor = None
        self.adjustSize()

    def _discard(self):
        self._discard_current("discarded")
        self.hide()

    def _discard_current(self, why):
        draft = self._draft
        self._draft = None
        self._editor = None
        self._pending_add = False
        self._ereq += 1          # in-flight edit UI results now drop
        self._tx_timer.stop()
        if draft is None:
            return
        # Let go of audio.wav (a Play may hold it) so rmtree can remove it on
        # Windows, then delete UNDER the lock: any running edit write finishes
        # first (ms), the folder is gone before we return -- so a later sync
        # can't sweep it -- and discard_draft nulls folder_path, so a still
        # queued edit that runs afterwards writes nothing (write_artifacts and
        # recut both bail on a missing folder).
        self._stop_playback()
        name = os.path.basename(draft.folder_path or "") or "draft"
        try:
            with self._disk_lock:
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
        self._editor = None
        self._pending_add = False
        self._ereq += 1
        self.hide()

    def _center(self):
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.center().x() - self.width() // 2,
                  max(screen.top() + 20,
                      screen.center().y() - self.height() // 2))


def _set_text(label, text):
    """A field label showing text, or the dim '— empty' the preview promises
    when there is none."""
    if text:
        label.setObjectName("fieldValue")
        label.setText(text)
    else:
        label.setObjectName("missing")
        label.setText("— empty")
    label.style().unpolish(label)
    label.style().polish(label)


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
