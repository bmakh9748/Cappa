"""First-run startup window -- and the future home of the settings panel.

Basic for now: pick the language clicked words are translated into, then start.
It's a normal titled window (not the transparent overlay), shown before the
overlay and reopened later via the launcher's Settings item to change a setting
live. To grow it, add a row here and a field in cappa.settings; the confirmed
signal already carries the chosen values back on the picked object."""

from PySide6.QtWidgets import (QApplication, QComboBox, QHBoxLayout, QLabel,
                               QPushButton, QSlider, QVBoxLayout, QWidget)
from PySide6.QtCore import Qt, Signal

from ..settings import (MAX_CLIP_RANGE, MIN_CLIP_RANGE, SOURCE_LANGUAGES,
                        TARGET_LANGUAGES)

_STYLE = """
    #startup {
        background: #12141c;
    }
    QLabel#title {
        color: #eaeaf0;
        font-size: 26px;
        font-weight: bold;
    }
    QLabel#subtitle {
        color: #9a9eb0;
        font-size: 13px;
    }
    QLabel#field {
        color: #c6cad8;
        font-size: 12px;
        font-weight: bold;
        padding-top: 4px;
    }
    QComboBox {
        color: #eaeaf0;
        background: rgba(255, 255, 255, 14);
        border: 1px solid rgba(255, 255, 255, 40);
        border-radius: 7px;
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
    QPushButton#primary {
        color: #06202b;
        background: #5ad2ff;
        border: none;
        border-radius: 7px;
        padding: 9px 22px;
        font-size: 13px;
        font-weight: bold;
    }
    QPushButton#primary:hover { background: #7bddff; }
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
"""


class StartupWindow(QWidget):
    # Emitted when the user confirms; the settings object passed in has been
    # updated in place with the chosen values.
    confirmed = Signal()

    def __init__(self, settings):
        super().__init__()
        self._settings = settings
        self.setObjectName("startup")
        self.setWindowTitle("Cappa")
        self.setAttribute(Qt.WA_StyledBackground, True)

        title = QLabel("Cappa")
        title.setObjectName("title")
        subtitle = QLabel("Turn subtitles into flashcards.")
        subtitle.setObjectName("subtitle")

        source_field = QLabel("Video language (what you're learning)")
        source_field.setObjectName("field")
        self._source_combo = QComboBox()
        for code, name in SOURCE_LANGUAGES:
            self._source_combo.addItem(name, code)
        i = self._source_combo.findData(settings.source_language)
        if i >= 0:
            self._source_combo.setCurrentIndex(i)

        target_field = QLabel("Translate words into (your language)")
        target_field.setObjectName("field")
        self._combo = QComboBox()
        for code, name in TARGET_LANGUAGES:
            self._combo.addItem(name, code)
        i = self._combo.findData(settings.target_language)
        if i >= 0:
            self._combo.setCurrentIndex(i)

        # Card audio clip length: min slider steps of 0.1s, max of 0.5s.
        # The row labels double as the live value readout.
        self._min_label = QLabel("")
        self._min_label.setObjectName("field")
        self._min_slider = QSlider(Qt.Horizontal)
        self._min_slider.setRange(int(MIN_CLIP_RANGE[0] * 10),
                                  int(MIN_CLIP_RANGE[1] * 10))
        self._min_slider.setValue(int(round(settings.min_clip_seconds * 10)))
        self._min_slider.valueChanged.connect(self._update_clip_labels)

        self._max_label = QLabel("")
        self._max_label.setObjectName("field")
        self._max_slider = QSlider(Qt.Horizontal)
        self._max_slider.setRange(int(MAX_CLIP_RANGE[0] * 2),
                                  int(MAX_CLIP_RANGE[1] * 2))
        self._max_slider.setValue(int(round(settings.max_clip_seconds * 2)))
        self._max_slider.valueChanged.connect(self._update_clip_labels)
        self._update_clip_labels()

        self._primary = QPushButton("Start Cappa")
        self._primary.setObjectName("primary")
        self._primary.setCursor(Qt.PointingHandCursor)
        self._primary.clicked.connect(self._on_confirm)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(30, 26, 30, 24)
        lay.setSpacing(6)
        lay.addWidget(title)
        lay.addWidget(subtitle)
        lay.addSpacing(16)
        lay.addWidget(source_field)
        lay.addWidget(self._source_combo)
        lay.addSpacing(10)
        lay.addWidget(target_field)
        lay.addWidget(self._combo)
        lay.addSpacing(10)
        lay.addWidget(self._min_label)
        lay.addWidget(self._min_slider)
        lay.addSpacing(10)
        lay.addWidget(self._max_label)
        lay.addWidget(self._max_slider)
        lay.addStretch(1)
        lay.addSpacing(16)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self._primary)
        lay.addLayout(buttons)

        self.setStyleSheet(_STYLE)
        self.resize(400, 470)
        self._center()

    def open_settings(self):
        """Reopen as a live settings panel (from the launcher's Settings item)."""
        self._primary.setText("Save settings")
        self._center()
        self.show()
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------ internals
    def _update_clip_labels(self, _value=0):
        self._min_label.setText("Shortest audio clip: %.1f s (one-word blips "
                                "are widened to this)"
                                % (self._min_slider.value() / 10.0))
        self._max_label.setText("Longest audio clip: %.1f s"
                                % (self._max_slider.value() / 2.0))

    def _on_confirm(self):
        self._settings.source_language = self._source_combo.currentData()
        self._settings.target_language = self._combo.currentData()
        self._settings.min_clip_seconds = self._min_slider.value() / 10.0
        self._settings.max_clip_seconds = self._max_slider.value() / 2.0
        self.confirmed.emit()

    def _center(self):
        screen = QApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(geo.center().x() - self.width() // 2,
                      geo.center().y() - self.height() // 2)
