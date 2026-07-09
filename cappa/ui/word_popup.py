"""The box that opens when a caption word is clicked.

Shows the word (edge punctuation stripped), a divider line, and the
translation. The translation is fetched on a helper thread so the UI never
blocks: the popup opens instantly with "Translating…" and fills in when the
call returns — or shows the failure (no network) as a ⚠ line. Below sits the
Create Anki card button: the screenshot is captured immediately when the word
is clicked, then clicking the button gathers the card's remaining ingredients
(word + sentence translations and the audio clip cut from the rolling recorder
buffer around when the sentence was on screen) via cappa.flashcard, also off
the UI thread. Saving the draft immediately triggers
cappa.flashcard.sync_to_anki() on the SAME background thread -- no separate
export step, no import dialog: with Anki open the card lands in it LIVE
through the AnkiConnect add-on, with Anki closed it goes straight into the
collection file for next launch. Only the new card is delivered (each card
folder keeps a receipt once it's in Anki); a sync problem is appended to
the button's message but the draft itself is never lost -- the receipt-less
card rides the next save's sync.

A child of the overlay, so it parks/hides with it and is excluded from
capture along with it. The overlay adds its geometry to the interactive
rects while visible, which is what lets its controls receive clicks. The
overlay supplies `region_provider` (the tracked area to screenshot) and
`recorder` (the audio ring buffer) — the popup owns the threading."""

import os
import threading

from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
                               QWidget)
from PySide6.QtCore import QPoint, QRect, Qt, Signal

from .. import flashcard
from ..detection.sentence import caption_block, click_pool
from ..dictionary import meaning
from ..translate import TranslationError, clean_word

MARGIN = 10           # gap between the word and the popup
MAX_TEXT_WIDTH = 300  # translation wraps instead of growing off-screen

_STYLE = """
    #wordPopup {
        background: rgba(18, 20, 28, 235);
        border: 1px solid rgba(255, 255, 255, 30);
        border-radius: 10px;
    }
    QLabel#word {
        color: #eaeaf0;
        font-size: 17px;
        font-weight: bold;
        padding: 2px 4px;
    }
    #divider {
        background: rgba(255, 255, 255, 36);
    }
    QLabel#translation {
        color: #c6cad8;
        font-size: 13px;
        padding: 0 2px;
    }
    QPushButton#anki {
        color: #bfe9ff;
        background: rgba(90, 210, 255, 26);
        border: 1px solid rgba(90, 210, 255, 90);
        border-radius: 6px;
        padding: 5px 12px;
        font-size: 12px;
        font-weight: bold;
    }
    QPushButton#anki:hover {
        background: rgba(90, 210, 255, 60);
    }
    QPushButton#anki:disabled {
        color: rgba(199, 201, 212, 110);
        background: rgba(255, 255, 255, 12);
        border-color: rgba(255, 255, 255, 26);
    }
    QPushButton#popupClose {
        color: #c7c9d4;
        background: rgba(255, 255, 255, 22);
        border: none;
        border-radius: 6px;
        padding: 3px 8px;
        font-size: 12px;
        font-weight: bold;
    }
    QPushButton#popupClose:hover {
        background: rgba(226, 76, 76, 190);
        color: #ffffff;
    }
"""


class WordPopup(QWidget):
    # (request id, translation, error) — emitted from the fetch thread; Qt
    # queues it onto the UI thread because the emitter is a foreign thread.
    _translated = Signal(int, str, str)
    # (request id, ok, message) from the card-build thread, same reason.
    _carded = Signal(int, bool, str)

    def __init__(self, parent, region_provider=None, recorder=None,
                 source=None, captions_provider=None):
        super().__init__(parent)
        self.setObjectName("wordPopup")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.word = None       # the detection Word currently shown
        self._anchor = QRect() # where it opened, for re-placing on growth
        self._req = 0          # stale-response guard: latest fetch wins
        # Data sources for the card, supplied by the overlay. region_provider()
        # -> physical (l, t, w, h) of the tracked area, or None; recorder is
        # the LoopbackRecorder (or None if audio is unavailable); source is the
        # active-video SourceSession (or None) that supplies caption-track audio.
        self._region_provider = region_provider
        self._recorder = recorder
        self._source = source
        # captions_provider() -> the live Sentence list; snapshotted at click
        # time so a two-line caption joins the clicked line on the card even
        # if the caption clears while the popup sits open, then reconciled
        # with the list as it stands at card time (click_pool) so a sibling
        # line detection hadn't finished re-reading at the click makes the
        # card too.
        self._captions_provider = captions_provider
        self._snapshot_png = None
        self._snapshot_note = ""
        self._snapshot_captions = []
        self._snapshot_play_time = None  # playback position frozen at click
                                         # time (it drifts while the popup sits)

        self._word_label = QLabel("", self)
        self._word_label.setObjectName("word")
        close = QPushButton("✕", self)
        close.setObjectName("popupClose")
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(self.hide)
        divider = QWidget(self)
        divider.setObjectName("divider")
        divider.setAttribute(Qt.WA_StyledBackground, True)
        divider.setFixedHeight(1)
        self._trans = QLabel("", self)
        self._trans.setObjectName("translation")
        self._trans.setWordWrap(True)
        self._trans.setMaximumWidth(MAX_TEXT_WIDTH)
        self._anki = QPushButton("Create Anki card", self)
        self._anki.setObjectName("anki")
        self._anki.setCursor(Qt.PointingHandCursor)
        self._anki.setEnabled(False)  # a card needs the translation
        self._anki.clicked.connect(self._create_card)

        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(self._word_label)
        head.addStretch(1)
        head.addWidget(close, alignment=Qt.AlignTop)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 12, 12)
        lay.setSpacing(8)
        lay.addLayout(head)
        lay.addWidget(divider)
        lay.addWidget(self._trans)
        lay.addWidget(self._anki, alignment=Qt.AlignLeft)
        self.setStyleSheet(_STYLE)
        self._translated.connect(self._fill)
        self._carded.connect(self._card_done)
        self.hide()

    def show_for(self, word, anchor):
        """Open for a detection Word, above `anchor` (a QRect in the
        parent's logical px), clamped inside the parent; below the anchor
        if there's no room. Kicks off the translation fetch. The Word stays
        on self.word — its .sentence is what the Anki card needs later."""
        self.word = word
        self._anchor = QRect(anchor)
        self._snapshot_png, self._snapshot_note = self._capture_click_image()
        self._snapshot_play_time = self._capture_play_time()
        self._snapshot_captions = (
            self._captions_provider() if self._captions_provider else None
        ) or []
        shown = clean_word(word.text) or word.text
        self._word_label.setText(shown)
        self._trans.setText("Translating…")
        self._anki.setText("Create Anki card")
        self._anki.setEnabled(False)
        self._req += 1
        # Translate with the WHOLE visible caption as context: a two-line
        # subtitle is one sentence split for layout, not two sentences.
        # Rows join with commas — they usually break at clause boundaries,
        # and Google parses comma'd clauses where it garbles the flat join
        # (card_0074); a mid-clause wrap tolerates the stray comma.
        if word.sentence:
            lines = caption_block(word.sentence, self._snapshot_captions)
            sentence = ", ".join(s.text for s in lines if s.text)
        else:
            sentence = ""
        threading.Thread(
            target=self._fetch, args=(self._req, shown, sentence),
            daemon=True,
        ).start()
        self._place()
        self.show()
        self.raise_()

    # ------------------------------------------------------------ internals
    def _capture_click_image(self):
        """Freeze the tracked video region at the word-click moment."""
        if not flashcard.prefs.include("screenshot"):
            return None, ""   # screenshots are off in the card settings
        try:
            region = (
                self._region_provider() if self._region_provider else None
            )
        except Exception as exc:
            return None, "click screenshot region failed: %s" % exc
        if region is None:
            return None, "no tracked area for a screenshot"
        try:
            data = flashcard.capture_png(region)
        except Exception as exc:
            return None, "click screenshot failed: %s" % exc
        if not data:
            return None, "click screenshot returned no image"
        return data, ""

    def _capture_play_time(self):
        """Freeze the video's playback position at click time. It both aims the
        caption search at where you are and feeds the position fallback. None
        when there's no source/bridge."""
        if self._source is None:
            return None
        try:
            return self._source.play_time()
        except Exception:
            return None

    def _fetch(self, req, word_text, sentence_text):
        """Helper thread: the blocking meaning lookup (dictionary first,
        contextual translation as fallback). Never touches widgets; the
        result crosses back through the queued _translated signal."""
        try:
            text, err = meaning(word_text, sentence_text), ""
        except TranslationError as exc:
            text, err = "", str(exc)
        except Exception:
            text, err = "", "translation failed"
        self._translated.emit(req, text, err)

    def _fill(self, req, text, err):
        if req != self._req or not self.isVisible():
            return  # popup moved on (new word / closed) while this ran
        self._trans.setText(("⚠ " + err) if err else text)
        self._anki.setEnabled(not err)
        self._place()  # the popup grew; re-clamp around the same word

    def _create_card(self):
        """Gather the card's pieces off the UI thread using the click-time
        screenshot already captured in show_for()."""
        if self.word is None:
            return
        word = self.word
        screenshot_png = self._snapshot_png
        screenshot_note = self._snapshot_note
        near_t = self._snapshot_play_time
        # The click snapshot can be a beat too early: a sibling line the
        # ledger was still re-reading at click time (card_0045 lost its top
        # line that way) is live by NOW, so reconcile the snapshot with the
        # current list before the block is assembled.
        captions = click_pool(
            self._snapshot_captions,
            self._captions_provider() if self._captions_provider else None,
            getattr(word, "sentence", None),
        )
        self._anki.setEnabled(False)
        self._anki.setText("Saving…")
        self._req += 1
        threading.Thread(
            target=self._build_card,
            args=(self._req, word, screenshot_png, screenshot_note, near_t,
                  captions),
            daemon=True,
        ).start()

    def _build_card(self, req, word, screenshot_png, screenshot_note, near_t,
                    captions):
        """Helper thread: the blocking gather (translations + WAV write).
        Result crosses back through the queued _carded signal."""
        try:
            draft = flashcard.build_draft(
                word, None, self._recorder,
                screenshot_png=screenshot_png,
                screenshot_note=screenshot_note,
                source=self._source,
                near_t=near_t,
                captions=captions,
            )
            print("[cappa] card: " + draft.summary())
            ok = draft.folder_path is not None
            folder = (os.path.basename(draft.folder_path)
                      if draft.folder_path else "draft")
            msg = "Saved %s" % folder
            if draft.notes:
                # Something degraded (audio fell back, no screenshot, ...) —
                # say so instead of a silent thumbs-up; details in the console.
                msg += " · %d note%s" % (len(draft.notes),
                                         "" if len(draft.notes) == 1 else "s")
            if not ok:
                msg = "Card failed"
            else:
                # The draft is saved either way; a sync problem is appended
                # to the message, never lost -- the undelivered card rides
                # the next save's sync pass.
                msg += " · " + self._sync_to_anki()
            self._carded.emit(req, ok, msg)
        except Exception as exc:
            self._carded.emit(req, False, "Card failed: %s" % exc)

    def _sync_to_anki(self):
        """Deliver the just-saved card (plus any earlier save Anki wasn't
        reachable for) -- live into the open app, or into its collection
        file when closed. Cards already delivered are never touched.
        Still on the card-build thread; never raises."""
        try:
            added = flashcard.sync_to_anki()
        except flashcard.SyncError as exc:
            print("[cappa] anki sync:", exc)
            return "Anki: %s" % exc
        except Exception as exc:
            print("[cappa] anki sync failed:", exc)
            return "Anki sync failed: %s" % exc
        result = "Anki: %d new" % added if added else "Anki: nothing new"
        print("[cappa] anki sync:", result)
        return result

    def _card_done(self, req, ok, message):
        if req != self._req or not self.isVisible():
            return
        self._anki.setText(message if len(message) < 40 else message[:39] + "…")
        # Re-enable so the user can retry (e.g. after fixing the network).
        self._anki.setEnabled(True)
        self._place()

    def _place(self):
        self.adjustSize()
        pw, ph = self.parentWidget().width(), self.parentWidget().height()
        a = self._anchor
        x = a.center().x() - self.width() // 2
        x = min(max(x, 4), max(pw - self.width() - 4, 4))
        y = a.top() - self.height() - MARGIN
        if y < 4:
            y = min(a.bottom() + MARGIN, max(ph - self.height() - 4, 4))
        self.move(QPoint(x, y))
