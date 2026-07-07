"""Flashcard draft creation.

This package gathers the pieces for a future Anki card: the clicked word, its
OCR sentence, translations, a click-time screenshot, and an audio clip. It
does not export .apkg files yet.

The map (one ingredient per file):

    builder.py     build_draft: assembles one card into cards/card_NNNN --
                   text, provenance, translations, snap-to-track correction
    clip.py        the audio: picks the caption window (text vs position
                   match) and cuts it -- source audio, loopback, fallbacks
    timing.py      window maths: pre/postroll, min/max clip (the appear/clear
                   lags come from detection/latency.py — they measure the
                   pipeline, not the card)
    model.py       CardDraft
    prefs.py       which fields a card collects and on which side (live copy
                   of the Flashcards settings tab)
    template.py    Anki-style card template: HTML faces + CSS, default design
    provenance.py  is the clicked word really in its saved sentence
    screenshot.py  click-time PNG capture/write
    writer.py      card_NNNN folders + metadata.json (the card's provenance
                   record -- add keys, never rename them)

Qt-free; the UI calls build_draft from a worker thread. Every missing piece
becomes a draft note, never an exception."""

from . import prefs
from .builder import CARDS_DIR, build_draft
from .model import CardDraft
from .screenshot import capture_png

__all__ = ["CARDS_DIR", "CardDraft", "build_draft", "capture_png", "prefs"]
