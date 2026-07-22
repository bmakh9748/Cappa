"""The box that opens when a caption word is clicked.

Shows the word (edge punctuation stripped), a 🔊 that pronounces it, and
three tabs: Meaning, Examples and Grammar. For JAPANESE the meaning is a real
dictionary entry, resolved offline from the JMdict pack the overlay already
looked the word up in: the headword and its reading, the part-of-speech
tags, the numbered senses, and — when the word on screen was inflected —
the chain that led back to the dictionary form (戻って, -te → 戻る). No
network call at all on that path.

Every other language keeps the old route: `dictionary.meaning` (Wiktionary
definitions, contextual Google translation as hint and fallback), fetched on
a helper thread so the UI never blocks — the popup opens instantly with
"Translating…" and fills in when the call returns, or shows the failure (no
network) as a ⚠ line. NO LLM on either path.

The Examples tab shows the word in real sentences (language/examples.py),
loaded lazily for a committed word with the tab in front (_refresh_examples
holds the contract). 🔊 speaks the headword via language/pronounce.py on a
helper thread; disabled when the language has no voice.

The Grammar tab is the word's anatomy per language (_grammar_html), under
the same laziness contract as Examples — Arabic's first analysis loads a
400 MB database.

Tab switches must never hide()/show() the popup — the overlay kills the
committed word highlight the moment the popup stops being visible — and a
switched tab re-runs _place(), because the popup's height is the height of
the page in front (_fit_tabs).

Two ways in. `show_for` is the COMMIT: it freezes the click moment and arms
the card button. `preview_for` is the live definition of a span the user is
still dragging out — same rendering, but it freezes nothing, calls nothing
over the network, and leaves the button disabled. Below sits the
Create Anki card button: the screenshot is captured immediately when the word
is clicked, then clicking the button gathers the card's remaining ingredients
(word + sentence translations and the audio clip cut from the rolling recorder
buffer around when the sentence was on screen) via cappa.flashcard, also off
the UI thread.

The gathered draft goes NOWHERE by itself: it opens the card preview
(ui/card_preview.py), which owns the two exits -- Add to Anki and Discard.

A child of the overlay, so it parks/hides with it and is excluded from
capture along with it. The overlay adds its geometry to the interactive
rects while visible, which is what lets its controls receive clicks. The
overlay supplies `region_provider` (the tracked area to screenshot) and
`recorder` (the audio ring buffer) — the popup owns the threading."""

import html
import re
import threading

from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QTabWidget, QVBoxLayout, QWidget)
from PySide6.QtCore import QPoint, QRect, Qt, Signal

from .. import arabic, flashcard, grammar_notes, indonesian
from ..detection.sentence import caption_block, click_pool
from ..language import examples, pronounce
from ..language import translate as translate_mod
from ..language.dictionary import meaning
from ..language.japanese import jmdict, kanjidic
from ..language.translate import TranslationError, clean_word
from .card_preview import CardPreview

MARGIN = 10           # gap between the word and the popup
MAX_TEXT_WIDTH = 320  # meanings wrap instead of growing off-screen
MAX_SENSES = 5        # a common verb has a dozen; a reader wants the top few

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
    QLabel#reading {
        color: #8a8fa2;
        font-size: 12px;
        padding: 6px 0 0 0;
    }
    QLabel#tags {
        color: #9fd6a6;
        font-size: 11px;
        padding: 0 2px;
    }
    QLabel#inflection {
        color: #8a8fa2;
        font-size: 11px;
        font-style: italic;
        padding: 0 2px;
    }
    QLabel#translation {
        color: #c6cad8;
        font-size: 13px;
        padding: 0 2px;
    }
    QLabel#examples {
        color: #c6cad8;
        font-size: 12px;
        padding: 0 2px;
    }
    QLabel#grammar {
        color: #c6cad8;
        font-size: 12px;
        padding: 0 2px;
    }
    QLabel#exsource {
        color: #5d6172;
        font-size: 10px;
        padding: 0 2px;
    }
    QTabWidget::pane {
        border: none;
        border-top: 1px solid rgba(255, 255, 255, 36);
    }
    QTabBar::tab {
        color: #8a8fa2;
        background: transparent;
        padding: 4px 10px;
        font-size: 11px;
        font-weight: bold;
        border: none;
        border-bottom: 2px solid transparent;
    }
    QTabBar::tab:selected {
        color: #5ad2ff;
        border-bottom: 2px solid #5ad2ff;
    }
    QTabBar::tab:hover:!selected {
        color: #c6cad8;
    }
    QPushButton#speak {
        color: #bfe9ff;
        background: rgba(90, 210, 255, 26);
        border: none;
        border-radius: 6px;
        padding: 2px 7px;
        font-size: 12px;
    }
    QPushButton#speak:hover {
        background: rgba(90, 210, 255, 60);
    }
    QPushButton#speak:disabled {
        color: rgba(199, 201, 212, 110);
        background: rgba(255, 255, 255, 12);
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


# --------------------------------------------------------- the Grammar tab
# Pure string builders (no widgets — they run on the popup's helper
# thread). Each returns rich text, or "" when the language has nothing to
# teach about the word; the popup shows a quiet "no notes" line then.

def _dim(text):
    return '<span style="color:#8a8fa2">%s</span>' % text


def _para(inner):
    return '<p style="margin:0 0 7px 0">%s</p>' % inner


def _grammar_html(surface, lemma):
    """The Grammar tab's content for a word, by the video's language:
    Japanese = the inflection chain explained + a per-kanji breakdown;
    Arabic = root, form (with its pattern one-liner), lemma and gloss;
    Indonesian = the root under the affixes, each affix explained."""
    lang = translate_mod.SOURCE_LANGUAGE
    if lang == jmdict.LANG:
        return _japanese_grammar(surface, lemma)
    if lang == arabic.LANG:
        return _arabic_grammar(surface)
    if lang == indonesian.LANG:
        return _indonesian_grammar(surface)
    return ""


def _japanese_grammar(surface, lemma):
    parts = []
    match = jmdict.word_at(surface, 0) if surface else None
    # Same agreement test as the Meaning tab's inflection line: the match
    # must cover the whole surface AND resolve to the lemma the overlay
    # committed — two tabs must never tell two stories about one word.
    covers = (match is not None and match.end == len(surface)
              and (not lemma or match.base == lemma))
    base = match.base if covers else (lemma or surface)
    if covers and match.reasons:
        steps = []
        for reason in match.reasons:
            note = jmdict.GRAMMAR_NOTES.get(reason)
            steps.append("<b>%s</b> — %s" % (html.escape(reason),
                                             html.escape(note))
                         if note else html.escape(reason))
        parts.append(_para("%s → %s<br>%s" % (
            html.escape(surface), html.escape(base), "<br>".join(steps))))
    for k in kanjidic.breakdown(base):
        meta = []
        if k.strokes:
            meta.append("%d strokes" % k.strokes)
        if k.grade:
            meta.append("Grade %d" % k.grade if k.grade <= 6
                        else ("Jōyō" if k.grade == 8 else "Names"))
        if k.jlpt:
            meta.append("JLPT %d (old)" % k.jlpt)
        readings = []
        if k.onyomi:
            readings.append("On " + "・".join(k.onyomi[:3]))
        if k.kunyomi:
            # '.' splits stem/okurigana and '-' marks suffix position in
            # KANJIDIC; neither reads well raw.
            readings.append("Kun " + "・".join(
                r.lstrip("-").replace(".", "") for r in k.kunyomi[:4]))
        line = "<b>%s</b>&nbsp; %s" % (
            html.escape(k.literal), html.escape("; ".join(k.meanings[:4])))
        for extra in (" · ".join(readings), " · ".join(meta)):
            if extra:
                line += "<br>" + _dim(html.escape(extra))
        parts.append(_para(line))
    if not parts and not kanjidic.ready():
        # No chain and no kanji rows BECAUSE the pack isn't here yet —
        # say so instead of an implausible "no notes" (rule 7).
        return _para(_dim("Kanji pack not downloaded yet — it fetches in "
                          "the background on a Japanese video."))
    return "".join(parts)


def _arabic_grammar(surface):
    analysis = arabic.analyze(surface)
    if analysis is None:
        # Unknown word, or no machinery at all? The difference must be
        # visible (rule 7) — status() is settled now that analyze() ran.
        reason = arabic.status()
        return _para(_dim(html.escape(reason))) if reason else ""
    head = []
    if analysis.loan:
        head.append(_dim("Loanword — no Arabic root"))
    elif analysis.root:
        head.append("Root: <b>%s</b>" % html.escape(analysis.root))
    lemma_line = html.escape(analysis.lemma)
    if analysis.pos:
        lemma_line += " " + _dim("(%s)" % html.escape(analysis.pos))
    head.append(lemma_line)
    if analysis.gloss:
        head.append(_dim(html.escape(analysis.gloss)))
    parts = [_para("<br>".join(head))]
    if analysis.form:
        note = grammar_notes.arabic_form_note(analysis.form)
        if note:
            pattern, translit, text = note
            parts.append(_para("Form %s — %s <i>%s</i><br>%s" % (
                html.escape(analysis.form), html.escape(pattern),
                html.escape(translit), _dim(html.escape(text)))))
        else:
            parts.append(_para("Form %s" % html.escape(analysis.form)))
    return "".join(parts)


def _indonesian_grammar(surface):
    got = indonesian.anatomy(surface)
    if got is None:
        reason = indonesian.status()   # settled by the anatomy() attempt
        return _para(_dim(html.escape(reason))) if reason else ""
    stem, labels = got
    notes = dict(grammar_notes.ID_AFFIX_NOTES)
    parts = [_para("Root: <b>%s</b>" % html.escape(stem))]
    for label in labels:
        note = notes.get(label)
        if note:
            parts.append(_para("<b>%s</b> — %s" % (
                html.escape(label), _dim(html.escape(note)))))
    return "".join(parts)


class WordPopup(QWidget):
    # (request id, translation, error) — emitted from the fetch thread; Qt
    # queues it onto the UI thread because the emitter is a foreign thread.
    _translated = Signal(int, str, str)
    # (draft or None, error message) from the card-build thread, same reason.
    # No request id: a built draft EXISTS on disk and must be resolved in the
    # preview whatever the popup has moved on to, or it syncs itself later.
    _built = Signal(object, str)
    # (request id, [Example], error) from the examples-fetch thread.
    _examples_ready = Signal(int, object, str)
    # (request id, rich text, error) from the grammar thread.
    _grammar_ready = Signal(int, str, str)
    # (request id, error) from the pronounce thread: re-arms 🔊.
    _spoken = Signal(int, str)

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
        self._preview = None   # the card preview window, built on first use

        self._ex_shown = None  # (text, lemma) the Examples tab was filled
                               # for — the lazy load's "already done" latch
        self._ex_offline = False  # pack pairs are on the tab right now, so
                                  # a failed top-up must not blank them
        self._gr_shown = None  # the Grammar tab's latch, same contract

        self._word_label = QLabel("", self)
        self._word_label.setObjectName("word")
        self._reading = QLabel("", self)   # 【もどる】, Japanese only
        self._reading.setObjectName("reading")
        self._speak = QPushButton("🔊", self)
        self._speak.setObjectName("speak")
        self._speak.setCursor(Qt.PointingHandCursor)
        self._speak.setEnabled(False)   # armed per word by _refresh_speak
        self._speak.clicked.connect(self._say_word)
        close = QPushButton("✕", self)
        close.setObjectName("popupClose")
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(self.hide)
        self._tags = QLabel("", self)      # Godan verb (-ru) · intransitive
        self._tags.setObjectName("tags")
        self._tags.setWordWrap(True)
        self._tags.setMaximumWidth(MAX_TEXT_WIDTH)
        self._inflection = QLabel("", self)  # 戻って → 戻る (-te)
        self._inflection.setObjectName("inflection")
        self._inflection.setWordWrap(True)
        self._inflection.setMaximumWidth(MAX_TEXT_WIDTH)
        self._trans = QLabel("", self)
        self._trans.setObjectName("translation")
        self._trans.setWordWrap(True)
        self._trans.setMaximumWidth(MAX_TEXT_WIDTH)
        self._examples = QLabel("", self)  # rich text: sentences + bolding
        self._examples.setObjectName("examples")
        self._examples.setTextFormat(Qt.RichText)
        self._examples.setWordWrap(True)
        self._examples.setMaximumWidth(MAX_TEXT_WIDTH)
        self._exsource = QLabel("", self)  # "from Tatoeba (CC BY)" credit
        self._exsource.setObjectName("exsource")
        self._exsource.setWordWrap(True)
        self._exsource.setMaximumWidth(MAX_TEXT_WIDTH)
        self._anki = QPushButton("Create Anki card", self)
        self._anki.setObjectName("anki")
        self._anki.setCursor(Qt.PointingHandCursor)
        self._anki.setEnabled(False)  # a card needs the translation
        self._anki.clicked.connect(self._create_card)

        # The tab pages. The pane's top border draws the divider the old
        # single-page popup had as its own widget.
        meaning_page = QWidget(self)
        mv = QVBoxLayout(meaning_page)
        mv.setContentsMargins(0, 8, 0, 0)
        mv.setSpacing(6)
        mv.addWidget(self._tags)
        mv.addWidget(self._trans)
        examples_page = QWidget(self)
        ev = QVBoxLayout(examples_page)
        ev.setContentsMargins(0, 8, 0, 0)
        ev.setSpacing(4)
        ev.addWidget(self._examples)
        ev.addWidget(self._exsource)
        self._grammar = QLabel("", self)   # rich text, built off-thread
        self._grammar.setObjectName("grammar")
        self._grammar.setTextFormat(Qt.RichText)
        self._grammar.setWordWrap(True)
        self._grammar.setMaximumWidth(MAX_TEXT_WIDTH)
        grammar_page = QWidget(self)
        gv = QVBoxLayout(grammar_page)
        gv.setContentsMargins(0, 8, 0, 0)
        gv.setSpacing(4)
        gv.addWidget(self._grammar)
        self._tabs = QTabWidget(self)
        self._tabs.addTab(meaning_page, "Meaning")
        self._tabs.addTab(examples_page, "Examples")
        self._tabs.addTab(grammar_page, "Grammar")
        self._tabs.currentChanged.connect(self._tab_changed)

        head = QHBoxLayout()
        head.setSpacing(6)
        head.addWidget(self._word_label)
        head.addWidget(self._reading, alignment=Qt.AlignBottom)
        head.addWidget(self._speak, alignment=Qt.AlignVCenter)
        head.addStretch(1)
        head.addWidget(close, alignment=Qt.AlignTop)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 12, 12)
        lay.setSpacing(6)
        lay.addLayout(head)
        lay.addWidget(self._inflection)
        lay.addWidget(self._tabs)
        lay.addSpacing(2)
        lay.addWidget(self._anki, alignment=Qt.AlignLeft)
        self.setStyleSheet(_STYLE)
        self._translated.connect(self._fill)
        self._built.connect(self._card_done)
        self._examples_ready.connect(self._examples_done)
        self._grammar_ready.connect(self._grammar_done)
        self._spoken.connect(self._spoken_done)
        self._fit_tabs()
        self.hide()

    def roots(self):
        """The preview's top-level hwnd while it is open — the overlay counts
        it as 'ours' so showing it doesn't park the tracking border."""
        return self._preview.roots() if self._preview else ()

    def show_for(self, word, anchor):
        """Open for a detection Word, above `anchor` (a QRect in the
        parent's logical px), clamped inside the parent; below the anchor
        if there's no room. The Word stays on self.word — its .sentence is
        what the Anki card needs later, and its .lemma (set by the overlay's
        dictionary lookup) is the form the card studies."""
        self.word = word
        self._anchor = QRect(anchor)
        self._snapshot_png, self._snapshot_note = self._capture_click_image()
        self._snapshot_play_time = self._capture_play_time()
        self._snapshot_captions = (
            self._captions_provider() if self._captions_provider else None
        ) or []
        surface = clean_word(word.text) or word.text
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

        entry = self._entry_for(word)
        if entry is not None:
            # Japanese: the dictionary already has the answer, offline.
            self._show_entry(word, surface, entry)
            self._anki.setEnabled(True)
        else:
            self._show_lookup(surface)
            threading.Thread(
                target=self._fetch, args=(self._req, surface, sentence),
                daemon=True,
            ).start()
        self._refresh_speak()
        self._refresh_examples(word, entry)
        self._reset_grammar()
        self._fit_tabs()
        self._place()
        self.show()
        self.raise_()

    def preview_for(self, word, anchor):
        """The definition of the span being dragged, live, while the button
        is still down.

        Deliberately side-effect free, because this runs on every tick the
        selection grows: it never freezes the click moment (no screenshot,
        no playback position, no caption snapshot) and never touches the
        network — a dictionary hit is a cached sqlite lookup, and a span the
        dictionary doesn't know just says so and waits for the release. The
        card button stays disabled: `self.word = None` means nothing here is
        committed, and only show_for() commits."""
        self.word = None
        self._anchor = QRect(anchor)
        self._req += 1     # a fetch still in flight must not paint over this
        entry = self._entry_for(word)
        if entry is not None:
            self._show_entry(word, clean_word(word.text) or word.text, entry)
        else:
            self._show_selection(word)
        self._anki.setText("Create Anki card")
        self._anki.setEnabled(False)
        self._refresh_speak()
        self._refresh_examples(word, entry)
        self._reset_grammar()
        self._fit_tabs()
        self._place()
        self.show()
        self.raise_()

    def _entry_for(self, word):
        """The JMdict entry for this word, or None when it isn't a resolved
        Japanese word (the overlay sets .lemma only when the pack answered)."""
        lemma = getattr(word, "lemma", None)
        if not lemma:
            return None
        entries = jmdict.lookup(lemma)
        return entries[0] if entries else None

    @staticmethod
    def _set(label, text):
        """Fill a line, and take it out of the layout when it's empty — an
        invisible label still costs its row's spacing."""
        label.setText(text)
        label.setVisible(bool(text))

    def _show_entry(self, word, surface, entry):
        """A reader's entry: headword, reading, tags, numbered senses — and
        how the inflected surface on screen got back to the headword."""
        self._set(self._word_label, entry.headword)
        self._set(self._reading,
                  "【%s】" % entry.reading
                  if entry.reading and entry.reading != entry.headword else "")
        # How the form ON SCREEN got back to the headword. Read off the
        # surface itself, so a hand-dragged span explains itself too.
        match = jmdict.word_at(word.text, 0) if word.text else None
        reasons = (match.reasons if match and match.end == len(word.text)
                   and match.base == word.lemma else ())
        self._set(self._inflection,
                  "%s — %s" % (surface, ", ".join(reasons)) if reasons else "")
        self._set(self._tags, " · ".join(entry.tags()))
        senses = []
        for i, (_pos, glosses) in enumerate(entry.senses[:MAX_SENSES], 1):
            senses.append("%d. %s" % (i, "; ".join(glosses[:3])))
        self._set(self._trans, "\n".join(senses))

    def _show_lookup(self, surface):
        self._set(self._word_label, surface)
        self._set(self._reading, "")
        self._set(self._inflection, "")
        self._set(self._tags, "")
        self._set(self._trans, "Translating…")

    def _show_selection(self, word):
        """A dragged span the dictionary has no entry for. Shown as-is; the
        translation waits for the button to come up, because a lookup per
        drag tick would be a network call per pixel."""
        self._set(self._word_label, clean_word(word.text) or word.text)
        self._set(self._reading, "")
        self._set(self._inflection, "")
        self._set(self._tags, "")
        self._set(self._trans, "No dictionary entry — release to translate")

    # ------------------------------------------------------------- the tabs
    def _fit_tabs(self):
        """QTabWidget's height is its TALLEST page unless the hidden pages
        are told not to count: without this, the Meaning tab would sit on
        dead space reserved for the Examples list. Ignored size policy on
        the back pages keeps the popup exactly as tall as the page in
        front; callers re-run _place() after."""
        current = self._tabs.currentIndex()
        for i in range(self._tabs.count()):
            page = self._tabs.widget(i)
            if i == current:
                page.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
            else:
                page.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self._tabs.updateGeometry()

    def _tab_changed(self, index):
        # NEVER hide()/show() here: the overlay drops the committed word
        # highlight the moment the popup stops being visible.
        self._fit_tabs()
        if index == 1:
            self._load_examples()
        elif index == 2:
            self._load_grammar()
        self._place()

    def _refresh_examples(self, word, entry):
        """Fill or arm the Examples tab for the word now shown. The JMdict
        pack's pairs ride on the entry already in hand — no extra cost, so
        they render immediately, previews included. The web sources cost
        seconds, so they wait until the word is committed AND the tab is in
        front; most clicks never open the tab and never pay. A pack crop
        thinner than the tab's LIMIT does NOT latch the tab as done: 98% of
        example-bearing entries hold one or two pairs, and opening the tab
        still owes them the Tatoeba top-up (which re-includes the pack
        pairs, so the render only ever grows)."""
        self._ex_shown = None
        self._ex_offline = False
        offline = entry.examples if entry is not None else ()
        if offline:
            form = clean_word(word.text) or word.text
            self._render_examples(
                [examples.Example(jp, en, form=used or form, source="Tatoeba")
                 for jp, en, used in offline[:examples.LIMIT]])
            self._ex_offline = True
            if len(offline) >= examples.LIMIT:
                self._ex_shown = (word.text, getattr(word, "lemma", None))
        elif self.word is None:
            # A drag preview never fetches (network per tick); it says so
            # with the same voice as the meaning line.
            self._set(self._examples, "Release to load examples")
            self._set(self._exsource, "")
        else:
            self._set(self._examples, "")
            self._set(self._exsource, "")
        if (self.word is not None and self._ex_shown is None
                and self._tabs.currentIndex() == 1):
            self._load_examples()

    def _reset_grammar(self):
        """Arm the Grammar tab for the word now shown — same laziness as
        the Examples tab: nothing is computed until the word is committed
        and the tab is actually in front (Arabic's first analysis loads a
        400 MB database; even the cheap languages don't need to run per
        drag tick)."""
        self._gr_shown = None
        if self.word is None:
            self._set(self._grammar, "Release to see grammar")
        else:
            self._set(self._grammar, "")
            if self._tabs.currentIndex() == 2:
                self._load_grammar()

    def _load_grammar(self):
        """Start the grammar build for the committed word, once."""
        word = self.word
        if word is None:
            return             # preview: nothing is committed
        key = (word.text, getattr(word, "lemma", None))
        if self._gr_shown == key:
            return
        self._gr_shown = key
        self._set(self._grammar, "Analyzing…")
        surface = clean_word(word.text) or word.text
        threading.Thread(
            target=self._fetch_grammar,
            args=(self._req, surface, getattr(word, "lemma", None)),
            daemon=True,
        ).start()

    def _fetch_grammar(self, req, surface, lemma):
        """Helper thread: build the tab's rich text (Arabic pays its
        one-time analyzer load here). Never touches widgets."""
        try:
            content, err = _grammar_html(surface, lemma), ""
        except Exception:
            content, err = "", "grammar lookup failed"
        self._grammar_ready.emit(req, content, err)

    def _grammar_done(self, req, content, err):
        if req != self._req or not self.isVisible():
            return
        if err:
            self._gr_shown = None   # reopening the tab retries
            self._set(self._grammar, "⚠ " + err)
        elif not content:
            # Empty can mean "a pack is still downloading on the worker
            # thread" — don't latch, so reopening the tab retries once
            # the pack lands (the rebuild is milliseconds for ja/id, and
            # a settled-absent analyzer answers instantly).
            self._gr_shown = None
            self._set(self._grammar, "No grammar notes for this word.")
        else:
            self._set(self._grammar, content)
        self._fit_tabs()
        self._place()

    def _load_examples(self):
        """Start the web fetch for the committed word's examples, once."""
        word = self.word
        if word is None:
            return             # preview: nothing is committed, nothing loads
        key = (word.text, getattr(word, "lemma", None))
        if self._ex_shown == key:
            return
        self._ex_shown = key
        if not self._ex_offline:
            # With pack pairs already on screen, they stay up while the
            # top-up runs; a blank tab says what it is doing instead.
            self._set(self._examples, "Loading examples…")
            self._set(self._exsource, "")
        surface = clean_word(word.text) or word.text
        threading.Thread(
            target=self._fetch_examples,
            args=(self._req, surface, getattr(word, "lemma", None)),
            daemon=True,
        ).start()

    def _fetch_examples(self, req, surface, lemma):
        """Helper thread: the blocking example lookup. Never touches
        widgets; the result crosses back through _examples_ready."""
        try:
            items, err = examples.sentences(surface, lemma), ""
        except examples.ExamplesError as exc:
            items, err = [], str(exc)
        except Exception:
            items, err = [], "examples failed"
        self._examples_ready.emit(req, items, err)

    def _examples_done(self, req, items, err):
        if req != self._req or not self.isVisible():
            return  # popup moved on (new word / closed) while this ran
        if err:
            self._ex_shown = None   # reopening the tab retries the fetch
            if not self._ex_offline:
                # Pack pairs already on screen beat an error line: keep
                # them, and only a blank tab reports the failure.
                self._set(self._examples, "⚠ " + err)
                self._set(self._exsource, "")
        elif not items:
            if not self._ex_offline:
                self._set(self._examples, "No example sentences found.")
                self._set(self._exsource, "")
        else:
            self._render_examples(items)
        self._fit_tabs()
        self._place()

    def _render_examples(self, items):
        parts = []
        for ex in items:
            line = self._bolded(ex.text, ex.form)
            if ex.translit:
                line += ('<br><span style="color:#8a8fa2; '
                         'font-style:italic">%s</span>'
                         % html.escape(ex.translit))
            if ex.translation:
                line += ('<br><span style="color:#8a8fa2">%s</span>'
                         % html.escape(ex.translation))
            parts.append('<p style="margin:0 0 7px 0">%s</p>' % line)
        self._set(self._examples, "".join(parts))
        names = " · ".join(dict.fromkeys(
            ex.source for ex in items if ex.source))
        credit = "from %s" % names if names else ""
        if "Tatoeba" in names:
            credit += " (CC BY)"    # Tatoeba sentences require attribution
        self._set(self._exsource, credit)

    @staticmethod
    def _bolded(text, form):
        """The sentence HTML-escaped, its first occurrence of the clicked
        form bolded (case-insensitive). No match — an inflection the exact
        search didn't literally contain — just renders plain. The match
        runs on the RAW text and each part is escaped separately: matching
        after escaping could land inside an entity ("amp" inside "&amp;")
        and corrupt the markup."""
        if form:
            m = re.search(re.escape(form), text, re.IGNORECASE)
            if m:
                return (html.escape(text[:m.start()]) + "<b>"
                        + html.escape(m.group(0)) + "</b>"
                        + html.escape(text[m.end():]))
        return html.escape(text)

    # ---------------------------------------------------------------- audio
    def _refresh_speak(self):
        """Arm 🔊 for the word now shown. Auto-detect has no voice to hand
        the TTS endpoint, so the button explains itself instead of failing
        on click."""
        self._speak.setText("🔊")
        can = (translate_mod.SOURCE_LANGUAGE != "auto"
               and bool(self._word_label.text()))
        self._speak.setEnabled(can)
        self._speak.setToolTip(
            "" if can else "Name the video's language in Settings to hear it")

    def _say_word(self):
        """Speak the shown headword on a helper thread (fetch + playback
        both block; the UI thread waits for neither)."""
        text = self._word_label.text()
        if not text:
            return
        self._speak.setEnabled(False)
        threading.Thread(
            target=self._speak_thread,
            args=(self._req, text, translate_mod.SOURCE_LANGUAGE),
            daemon=True,
        ).start()

    def _speak_thread(self, req, text, lang):
        try:
            # The fetch can outlive the word: audio for a popup that moved
            # on stays unplayed (reading _req from here is safe — an int
            # read under the GIL — and worst case plays one stale clip).
            pronounce.say(text, lang,
                          still_wanted=lambda: req == self._req)
            err = ""
        except pronounce.PronounceError as exc:
            err = str(exc)
        except Exception:
            err = "audio failed"
        self._spoken.emit(req, err)

    def _spoken_done(self, req, err):
        if req != self._req:
            return  # a fresh word already re-armed the button
        self._speak.setEnabled(True)
        if err:
            self._speak.setText("⚠")
            self._speak.setToolTip(err)
            print("[cappa] pronounce: " + err)
        else:
            # A retry that succeeded must shed the previous failure's ⚠.
            self._speak.setText("🔊")
            self._speak.setToolTip("")

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
        self._set(self._trans, ("⚠ " + err) if err else text)
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
        self._anki.setText("Building…")
        threading.Thread(
            target=self._build_card,
            args=(word, screenshot_png, screenshot_note, near_t, captions),
            daemon=True,
        ).start()

    def _build_card(self, word, screenshot_png, screenshot_note, near_t,
                    captions):
        """Helper thread: the blocking gather (translations + WAV write).
        Nothing is delivered here — the draft crosses back through the queued
        _built signal and the preview decides its fate."""
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
            if draft.folder_path is None:
                self._built.emit(None, "Card failed")
            else:
                self._built.emit(draft, "")
        except Exception as exc:
            self._built.emit(None, "Card failed: %s" % exc)

    def _card_done(self, draft, error):
        """Open the preview for the built draft. Deliberately NOT guarded on
        the popup still being visible: the draft is already a folder on disk,
        and only the preview can add it to Anki or delete it."""
        self._anki.setText("Create Anki card")
        self._anki.setEnabled(True)
        self._place()
        if draft is None:
            self._anki.setText(error[:39] + "…" if len(error) > 40 else error)
            return
        if self._preview is None:
            self._preview = CardPreview()
        self._preview.show_draft(draft)

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
