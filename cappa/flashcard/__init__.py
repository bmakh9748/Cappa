"""Flashcard draft creation.

This package gathers the pieces for a future Anki card: the clicked word, its
OCR sentence, translations, a click-time screenshot, and an audio clip. It
does not export .apkg files yet.
"""

from . import prefs
from .builder import CARDS_DIR, build_draft
from .model import CardDraft
from .screenshot import capture_png

__all__ = ["CARDS_DIR", "CardDraft", "build_draft", "capture_png", "prefs"]
