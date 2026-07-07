"""First-run startup window -- and the home of the settings panel.

Two tabs: Languages (what you're learning / what you translate into) and
Flashcards (what goes on a card and on which side, plus audio clip length).
It's a normal titled window (not the transparent overlay), shown before the
overlay and reopened later via the launcher's Settings item to change a
setting live.

The footer is a plain settings dialog's: Cancel and Save, equal weight, and
both know whether anything actually changed -- Save is greyed out until the
controls differ from the saved settings (_pending vs _clean). Start Cappa
is not a footer button at all: it lives inside the Languages page, first
run only, so it is never an option among the flashcard settings and no
other button ever takes its place. To grow the panel, add a field in
cappa.settings, a row in the right tab here, and its lines in
_load_from_settings / _apply_to_settings / _pending."""

from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFrame,
                               QHBoxLayout, QLabel, QPushButton, QSlider,
                               QTabWidget, QVBoxLayout, QWidget)
from PySide6.QtCore import Qt, QTimer, Signal

from ..flashcard.template import default_template, infer_placements
from ..settings import (CARD_BACK, CARD_FIELDS, CARD_FRONT, CARD_OFF,
                        MAX_CLIP_RANGE, MIN_CLIP_RANGE, SOURCE_LANGUAGES,
                        TARGET_LANGUAGES, valid_card_template)
from .template_dialog import TemplateDialog

_PLACEMENT_NAMES = ((CARD_FRONT, "Front"), (CARD_BACK, "Back"),
                    (CARD_OFF, "Off"))

_STYLE = """
    #startup {
        background: #12141c;
    }
    QLabel#title {
        color: #eaeaf0;
        font-size: 24px;
        font-weight: bold;
    }
    QLabel#subtitle {
        color: #9a9eb0;
        font-size: 13px;
    }
    QLabel#section {
        color: #7d8296;
        font-size: 11px;
        font-weight: bold;
    }
    QLabel#field {
        color: #c6cad8;
        font-size: 12px;
        font-weight: bold;
    }
    QLabel#field:disabled {
        color: #5a5e6e;
    }
    QLabel#hint {
        color: #9a9eb0;
        font-size: 11px;
    }
    QTabWidget::pane {
        border: none;
        border-top: 1px solid rgba(255, 255, 255, 26);
    }
    QTabBar::tab {
        color: #9a9eb0;
        background: transparent;
        padding: 8px 16px;
        font-size: 12px;
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
    QComboBox {
        color: #eaeaf0;
        background: rgba(255, 255, 255, 14);
        border: 1px solid rgba(255, 255, 255, 40);
        border-radius: 8px;
        padding: 7px 10px;
        font-size: 13px;
    }
    QComboBox:hover { border-color: rgba(90, 210, 255, 120); }
    QComboBox QAbstractItemView {
        color: #eaeaf0;
        background: #1b1e28;
        selection-background-color: rgba(90, 210, 255, 60);
        border: 1px solid rgba(255, 255, 255, 30);
        outline: none;
    }
    QCheckBox {
        color: #c6cad8;
        font-size: 12px;
        font-weight: bold;
        spacing: 8px;
    }
    QCheckBox:disabled { color: #5a5e6e; }
    QCheckBox::indicator {
        width: 14px;
        height: 14px;
        border-radius: 4px;
        border: 1px solid rgba(255, 255, 255, 60);
        background: rgba(255, 255, 255, 10);
    }
    QCheckBox::indicator:checked {
        background: #5ad2ff;
        border-color: #5ad2ff;
    }
    QCheckBox::indicator:disabled {
        border-color: rgba(255, 255, 255, 26);
        background: rgba(255, 255, 255, 6);
    }
    QFrame#panelCard {
        background: rgba(255, 255, 255, 7);
        border: 1px solid rgba(255, 255, 255, 22);
        border-radius: 9px;
    }
    QFrame#footerLine {
        background: rgba(255, 255, 255, 22);
        border: none;
    }
    QPushButton#primary {
        color: #06202b;
        background: #5ad2ff;
        border: 1px solid transparent;
        border-radius: 8px;
        padding: 9px 0;
        font-size: 13px;
        font-weight: bold;
    }
    QPushButton#primary:hover { background: #7bddff; }
    QPushButton#primary:disabled {
        color: rgba(234, 234, 240, 80);
        background: rgba(255, 255, 255, 18);
    }
    QPushButton#ghost {
        color: #c6cad8;
        background: transparent;
        border: 1px solid rgba(255, 255, 255, 60);
        border-radius: 8px;
        padding: 9px 0;
        font-size: 13px;
        font-weight: bold;
    }
    QPushButton#ghost:hover {
        color: #eaeaf0;
        background: rgba(255, 255, 255, 10);
        border-color: rgba(255, 255, 255, 110);
    }
    QPushButton#ghost:disabled {
        color: #5a5e6e;
        border-color: rgba(255, 255, 255, 26);
    }
    QPushButton#edit {
        color: #c6cad8;
        background: transparent;
        border: 1px solid rgba(255, 255, 255, 60);
        border-radius: 8px;
        padding: 7px 16px;
        font-size: 12px;
        font-weight: bold;
    }
    QPushButton#edit:hover {
        color: #eaeaf0;
        background: rgba(255, 255, 255, 10);
        border-color: rgba(255, 255, 255, 110);
    }
    QSlider::groove:horizontal {
        height: 4px;
        background: rgba(255, 255, 255, 30);
        border-radius: 2px;
    }
    QSlider::sub-page:horizontal {
        background: rgba(90, 210, 255, 90);
        border-radius: 2px;
    }
    QSlider::handle:horizontal {
        width: 14px;
        margin: -6px 0;
        border-radius: 7px;
        background: #5ad2ff;
    }
    QSlider::handle:horizontal:disabled { background: #5a5e6e; }
    QSlider::sub-page:horizontal:disabled {
        background: rgba(255, 255, 255, 30);
    }
"""

_FOOTER_BTN_WIDTH = 118   # Cancel and Save, identical on purpose


class StartupWindow(QWidget):
    # The settings object passed in is updated in place before either of
    # the first two fire. started: first-run "Start Cappa" (save + launch).
    # saved: "Save" (persist; the app hides the panel when the overlay is
    # already running). cancelled: edits were reverted.
    started = Signal()
    saved = Signal()
    cancelled = Signal()

    def __init__(self, settings):
        super().__init__()
        self._settings = settings
        self._started = False   # flips on Start; the Start button retires
        self._clean = None      # _pending() snapshot of the saved state
        self._card_template = None   # properly set by _load_from_settings;
                                     # exists early because loading the
                                     # combos fires _refresh_buttons
        self._use_custom = False     # ditto; whether _card_template drives
                                     # the cards or just sits saved
        self.setObjectName("startup")
        self.setWindowTitle("Cappa")
        self.setAttribute(Qt.WA_StyledBackground, True)

        title = QLabel("Cappa")
        title.setObjectName("title")
        subtitle = QLabel("Turn subtitles into flashcards.")
        subtitle.setObjectName("subtitle")

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_languages_tab(), "Languages")
        self._tabs.addTab(self._build_flashcards_tab(), "Flashcards")

        footer_line = QFrame()
        footer_line.setObjectName("footerLine")
        footer_line.setFixedHeight(1)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setObjectName("ghost")
        self._save_btn = QPushButton("Save")
        self._save_btn.setObjectName("primary")
        for btn, handler in ((self._cancel_btn, self._on_cancel),
                             (self._save_btn, self._on_save)):
            btn.setFixedWidth(_FOOTER_BTN_WIDTH)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(handler)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(6)
        lay.addWidget(title)
        lay.addWidget(subtitle)
        lay.addSpacing(12)
        lay.addWidget(self._tabs, 1)
        lay.addSpacing(14)
        lay.addWidget(footer_line)
        lay.addSpacing(12)
        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addStretch(1)
        buttons.addWidget(self._cancel_btn)
        buttons.addWidget(self._save_btn)
        lay.addLayout(buttons)

        # Any control edit re-judges the footer: Save lights up only when
        # something actually differs from the saved settings.
        for combo in (self._source_combo, self._combo,
                      *self._field_combos.values()):
            combo.currentIndexChanged.connect(self._refresh_buttons)
        for slider in (self._min_slider, self._max_slider):
            slider.valueChanged.connect(self._refresh_buttons)

        self._load_from_settings()
        self.setStyleSheet(_STYLE)
        self.resize(440, 620)
        self._center()

    def open_settings(self):
        """Reopen as a live settings panel (from the launcher's Settings
        item). Always opens clean: edits abandoned last time are reloaded
        from the saved settings."""
        self._started = True
        self._start_btn.hide()
        self._start_hint.hide()
        self._load_from_settings()
        self._center()
        self.show()
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------ tabs
    def _build_languages_tab(self):
        source_field = QLabel("Video language (what you're learning)")
        source_field.setObjectName("field")
        self._source_combo = QComboBox()
        for code, name in SOURCE_LANGUAGES:
            self._source_combo.addItem(name, code)

        target_field = QLabel("Translate words into (your language)")
        target_field.setObjectName("field")
        self._combo = QComboBox()
        for code, name in TARGET_LANGUAGES:
            self._combo.addItem(name, code)

        # First-run only: starting the app is this page's own big action,
        # not a footer button -- so it never appears among the flashcard
        # settings and Save/Cancel never swap into its place.
        self._start_hint = QLabel("You can change everything later from the "
                                  "launcher's Settings.")
        self._start_hint.setObjectName("hint")
        self._start_btn = QPushButton("Start Cappa")
        self._start_btn.setObjectName("primary")
        self._start_btn.setCursor(Qt.PointingHandCursor)
        self._start_btn.clicked.connect(self._on_start)

        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(0, 16, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(source_field)
        lay.addWidget(self._source_combo)
        lay.addSpacing(12)
        lay.addWidget(target_field)
        lay.addWidget(self._combo)
        lay.addStretch(1)
        lay.addWidget(self._start_hint)
        lay.addSpacing(2)
        lay.addWidget(self._start_btn)
        return tab

    def _build_flashcards_tab(self):
        """Card design (advanced) on top, then one row per card field --
        where it goes (Front/Back) or Off to skip gathering it entirely --
        then the audio clip length sliders."""
        design_title = QLabel("Card design")
        design_title.setObjectName("field")
        self._template_status = QLabel("")
        self._template_status.setObjectName("hint")
        # Default <-> Custom switch: a saved custom design stays stored
        # while Default is selected, ready to switch back to. Disabled
        # until a custom design exists (Edit... creates one).
        self._design_combo = QComboBox()
        self._design_combo.addItem("Default", False)
        self._design_combo.addItem("Custom", True)
        self._design_combo.setFixedWidth(96)
        self._design_combo.currentIndexChanged.connect(
            self._design_mode_changed)
        template_btn = QPushButton("Edit…")
        template_btn.setObjectName("edit")
        template_btn.setCursor(Qt.PointingHandCursor)
        template_btn.clicked.connect(self._edit_template)
        design_card = QFrame()
        design_card.setObjectName("panelCard")
        card_lay = QHBoxLayout(design_card)
        card_lay.setContentsMargins(14, 10, 14, 10)
        card_lay.setSpacing(8)
        card_text = QVBoxLayout()
        card_text.setSpacing(2)
        card_text.addWidget(design_title)
        card_text.addWidget(self._template_status)
        card_lay.addLayout(card_text, 1)
        card_lay.addWidget(self._design_combo)
        card_lay.addWidget(template_btn)

        content_section = QLabel("CARD CONTENT")
        content_section.setObjectName("section")
        content_hint = QLabel("Front and Back are the card's sides; "
                              "Off skips collecting that piece.")
        content_hint.setObjectName("hint")
        self._field_combos = {}
        rows = []
        for key, label_text, _default in CARD_FIELDS:
            label = QLabel(label_text)
            label.setObjectName("field")
            combo = QComboBox()
            for value, name in _PLACEMENT_NAMES:
                combo.addItem(name, value)
            combo.setFixedWidth(104)
            self._field_combos[key] = combo
            row = QHBoxLayout()
            row.addWidget(label)
            row.addStretch(1)
            row.addWidget(combo)
            rows.append(row)

        # Card audio clip length: min slider steps of 0.1s, max of 0.5s.
        # The row labels double as the live value readout. Greyed out while
        # audio is off -- the bounds only matter when a clip is cut. Auto
        # (the confusion-proof default) fits the clip to the sentence and
        # retires the max slider.
        audio_section = QLabel("AUDIO CLIP LENGTH")
        audio_section.setObjectName("section")
        self._min_label = QLabel("")
        self._min_label.setObjectName("field")
        self._min_slider = QSlider(Qt.Horizontal)
        self._min_slider.setRange(int(MIN_CLIP_RANGE[0] * 10),
                                  int(MIN_CLIP_RANGE[1] * 10))
        self._min_slider.valueChanged.connect(self._update_clip_labels)

        self._auto_check = QCheckBox("Auto: fit the whole sentence")
        self._auto_check.setCursor(Qt.PointingHandCursor)
        self._auto_check.toggled.connect(self._update_audio_controls)
        self._auto_check.toggled.connect(self._refresh_buttons)

        self._max_label = QLabel("")
        self._max_label.setObjectName("field")
        self._max_slider = QSlider(Qt.Horizontal)
        self._max_slider.setRange(int(MAX_CLIP_RANGE[0] * 2),
                                  int(MAX_CLIP_RANGE[1] * 2))
        self._max_slider.valueChanged.connect(self._update_clip_labels)

        self._field_combos["audio"].currentIndexChanged.connect(
            self._update_audio_controls)

        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(0, 16, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(design_card)
        lay.addSpacing(12)
        lay.addWidget(content_section)
        lay.addWidget(content_hint)
        lay.addSpacing(4)
        for row in rows:
            lay.addLayout(row)
        lay.addSpacing(14)
        lay.addWidget(audio_section)
        lay.addSpacing(2)
        lay.addWidget(self._min_label)
        lay.addWidget(self._min_slider)
        lay.addSpacing(10)
        lay.addWidget(self._auto_check)
        lay.addSpacing(6)
        lay.addWidget(self._max_label)
        lay.addWidget(self._max_slider)
        lay.addStretch(1)
        return tab

    # ------------------------------------------------------------ state
    def _pending(self):
        """The controls' current values, comparable against _clean to know
        whether there is anything to save."""
        template = self._card_template
        return (
            self._source_combo.currentData(),
            self._combo.currentData(),
            self._min_slider.value(),
            self._max_slider.value(),
            self._auto_check.isChecked(),
            tuple((key, combo.currentData())
                  for key, combo in self._field_combos.items()),
            tuple(sorted(template.items())) if template else None,
            self._use_custom,
        )

    def _load_from_settings(self):
        """Point every control at the saved settings. The initial fill,
        Cancel, and reopening the panel all land on this same clean state."""
        s = self._settings
        for combo, value in ((self._source_combo, s.source_language),
                             (self._combo, s.target_language)):
            i = combo.findData(value)
            if i >= 0:
                combo.setCurrentIndex(i)
        self._min_slider.setValue(int(round(s.min_clip_seconds * 10)))
        self._max_slider.setValue(int(round(s.max_clip_seconds * 2)))
        self._auto_check.setChecked(s.auto_clip)
        self._update_clip_labels()
        for key, combo in self._field_combos.items():
            i = combo.findData(s.card_fields.get(key))
            if i >= 0:
                combo.setCurrentIndex(i)
        # The pending card design: the stored custom template (or None) and
        # whether it is the one in use. Written back only on Save/Start.
        self._card_template = s.card_template
        self._use_custom = s.use_custom_template
        self._update_audio_controls()
        self._update_design_controls()
        self._clean = self._pending()
        self._refresh_buttons()

    def _apply_to_settings(self):
        """Write the controls' pending values onto the settings object."""
        self._settings.source_language = self._source_combo.currentData()
        self._settings.target_language = self._combo.currentData()
        self._settings.min_clip_seconds = self._min_slider.value() / 10.0
        self._settings.max_clip_seconds = self._max_slider.value() / 2.0
        self._settings.auto_clip = self._auto_check.isChecked()
        self._settings.card_fields = {
            key: combo.currentData()
            for key, combo in self._field_combos.items()
        }
        self._settings.card_template = self._card_template
        self._settings.use_custom_template = (
            self._use_custom and self._card_template is not None)
        self._clean = self._pending()
        self._refresh_buttons()

    def _refresh_buttons(self, _value=0):
        """Save lights up only with something to save; Cancel only with
        something to revert -- or, once the app runs, a panel to close."""
        dirty = self._pending() != self._clean
        self._save_btn.setEnabled(dirty)
        self._cancel_btn.setEnabled(dirty or self._started)

    # ------------------------------------------------------------ actions
    def _on_start(self):
        self._apply_to_settings()
        self._started = True
        self._start_btn.hide()
        self._start_hint.hide()
        self._refresh_buttons()
        self.started.emit()

    def _on_save(self):
        self._apply_to_settings()
        self.saved.emit()
        if self.isVisible():
            # First run: the window stays open, so show that the save took.
            self._save_btn.setText("Saved")
            QTimer.singleShot(1200, lambda: self._save_btn.setText("Save"))

    def _on_cancel(self):
        self._load_from_settings()
        self.cancelled.emit()

    # ------------------------------------------------------------ internals
    def _update_clip_labels(self, _value=0):
        self._min_label.setText("Shortest clip: %.1f s (one-word blips are "
                                "widened to this)"
                                % (self._min_slider.value() / 10.0))
        if self._auto_check.isChecked():
            self._max_label.setText("Longest clip: auto — the whole "
                                    "sentence (up to 5 s)")
        else:
            self._max_label.setText("Longest clip: %.1f s"
                                    % (self._max_slider.value() / 2.0))

    def _update_audio_controls(self, _value=0):
        """Grey out what can't matter: everything while audio is off, the
        max slider while Auto length fits the sentence instead."""
        on = self._field_combos["audio"].currentData() != CARD_OFF
        for w in (self._min_label, self._min_slider, self._auto_check):
            w.setEnabled(on)
        manual = on and not self._auto_check.isChecked()
        self._max_label.setEnabled(on)
        self._max_slider.setEnabled(manual)
        self._update_clip_labels()

    def _auto_template(self):
        """The default design for the placements as picked RIGHT NOW in the
        combos (not yet confirmed) -- what auto mode would produce."""
        return default_template({key: combo.currentData()
                                 for key, combo in self._field_combos.items()})

    def _sync_rows_to_template(self, template):
        """Point the Front/Back/Off rows at what a design actually shows,
        so the panel displays -- and the builder gathers -- what the card
        will hold."""
        for key, value in infer_placements(template).items():
            combo = self._field_combos[key]
            i = combo.findData(value)
            if i >= 0:
                combo.setCurrentIndex(i)

    def _design_mode_changed(self, _index=0):
        """The Default <-> Custom switch. Switching away keeps the custom
        design stored; switching back re-syncs the rows to it, since the
        active design decides what the card holds."""
        self._use_custom = bool(self._design_combo.currentData())
        if self._use_custom and self._card_template:
            self._sync_rows_to_template(self._card_template)
        self._update_design_controls()
        self._refresh_buttons()

    def _edit_template(self):
        editing_custom = self._use_custom and self._card_template is not None
        current = (self._card_template if editing_custom
                   else self._auto_template())
        dlg = TemplateDialog(self, current, self._auto_template)
        if not dlg.exec():
            return
        values = valid_card_template(dlg.values())
        if values is None or values == self._auto_template():
            # Nothing custom came out of the editor: use the default. A
            # stored-but-inactive design stays saved for the switch; the
            # ACTIVE one was just edited into the default, so it's gone.
            self._use_custom = False
            if editing_custom:
                self._card_template = None
        else:
            self._card_template = values
            self._use_custom = True
            self._sync_rows_to_template(values)
        self._update_design_controls()
        self._refresh_buttons()

    def _update_design_controls(self):
        """Point the design switch + status line at the pending state."""
        has_custom = self._card_template is not None
        self._design_combo.blockSignals(True)
        self._design_combo.setCurrentIndex(
            self._design_combo.findData(self._use_custom and has_custom))
        self._design_combo.blockSignals(False)
        self._design_combo.setEnabled(has_custom)
        if self._use_custom and has_custom:
            status = "Custom design in use"
        elif has_custom:
            status = "Default design; your custom design stays saved"
        else:
            status = "Default design, follows the card content below"
        self._template_status.setText(status)

    def _center(self):
        screen = QApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(geo.center().x() - self.width() // 2,
                      geo.center().y() - self.height() // 2)
