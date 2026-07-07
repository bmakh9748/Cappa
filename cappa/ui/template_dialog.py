"""Advanced card-design editor: the Anki-style template, editable.

Opened from the Flashcards settings tab. Three editors -- front HTML, back
HTML, shared CSS -- prefilled with the design currently in effect. Save is
greyed out until the text differs from what the dialog opened with. Saving
a design identical to the auto default keeps the template in auto mode (it
goes on following the field placements); any other content becomes a custom
template used verbatim. "Reset to match settings" refills the editors from
the placements as they stand in the panel right now."""

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPlainTextEdit,
                               QPushButton, QVBoxLayout)
from PySide6.QtCore import Qt

_STYLE = """
    #templateDialog {
        background: #12141c;
    }
    QLabel#field {
        color: #c6cad8;
        font-size: 12px;
        font-weight: bold;
        padding-top: 4px;
    }
    QLabel#hint {
        color: #9a9eb0;
        font-size: 11px;
    }
    QPlainTextEdit {
        color: #eaeaf0;
        background: rgba(255, 255, 255, 10);
        border: 1px solid rgba(255, 255, 255, 40);
        border-radius: 8px;
        padding: 6px;
        font-size: 12px;
    }
    QPlainTextEdit:focus { border-color: rgba(90, 210, 255, 120); }
    QPushButton#primary {
        color: #06202b;
        background: #5ad2ff;
        border: 1px solid transparent;
        border-radius: 8px;
        padding: 8px 0;
        font-size: 12px;
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
        padding: 8px 0;
        font-size: 12px;
        font-weight: bold;
    }
    QPushButton#ghost:hover {
        color: #eaeaf0;
        background: rgba(255, 255, 255, 10);
        border-color: rgba(255, 255, 255, 110);
    }
"""

_BTN_WIDTH = 104   # Cancel and Save, identical on purpose


class TemplateDialog(QDialog):
    def __init__(self, parent, template, auto_provider):
        """template is the {"front", "back", "css"} design to show;
        auto_provider() returns the auto default for the placements as
        currently picked in the settings panel (for the reset button)."""
        super().__init__(parent)
        self._auto_provider = auto_provider
        self.setObjectName("templateDialog")
        self.setWindowTitle("Card design")

        hint = QLabel(
            "Anki-style card design: HTML for each face, one CSS sheet for "
            "both. {{Word}}-style tags are filled per card; "
            "{{#Field}}...{{/Field}} shows only when the field has content. "
            "Saving updates the Front/Back/Off rows to match your design "
            "(a field on neither face turns off).")
        hint.setObjectName("hint")
        hint.setWordWrap(True)

        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        self._editors = {}
        self._opened_with = dict(template)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(6)
        lay.addWidget(hint)
        for key, label_text, stretch in (("front", "Front", 2),
                                         ("back", "Back", 2),
                                         ("css", "Styling (CSS)", 3)):
            label = QLabel(label_text)
            label.setObjectName("field")
            editor = QPlainTextEdit()
            editor.setFont(mono)
            editor.setPlainText(template.get(key, ""))
            editor.textChanged.connect(self._refresh_save)
            self._editors[key] = editor
            lay.addWidget(label)
            lay.addWidget(editor, stretch)

        reset = QPushButton("Reset to match settings")
        reset.setObjectName("ghost")
        reset.setFixedWidth(190)
        reset.clicked.connect(self._reset)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("ghost")
        cancel.clicked.connect(self.reject)
        self._save = QPushButton("Save")
        self._save.setObjectName("primary")
        self._save.clicked.connect(self.accept)
        for btn in (cancel, self._save):
            btn.setFixedWidth(_BTN_WIDTH)
        for btn in (reset, cancel, self._save):
            btn.setCursor(Qt.PointingHandCursor)
        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addWidget(reset)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(self._save)
        lay.addSpacing(6)
        lay.addLayout(buttons)

        self.setStyleSheet(_STYLE)
        self.resize(560, 640)
        self._refresh_save()

    def values(self):
        return {key: editor.toPlainText()
                for key, editor in self._editors.items()}

    def _refresh_save(self):
        self._save.setEnabled(self.values() != self._opened_with)

    def _reset(self):
        auto = self._auto_provider()
        for key, editor in self._editors.items():
            editor.setPlainText(auto.get(key, ""))
