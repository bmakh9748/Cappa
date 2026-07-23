"""Flashcard draft creation, plus putting each saved card into Anki.

This package gathers the pieces for an Anki card: the clicked word, its OCR
sentence, translations, a click-time screenshot, and an audio clip -- then
puts the finished card into Anki, all riding the one Create Anki card button.

The map (one ingredient per file):

    builder.py     build_draft: assembles one card into cards/card_NNNN --
                   text, sentence provenance, translations, snap-to-track
                   correction, click-time PNG capture/write, and the two
                   opt-in extras (grammar breakdown, TTS word audio)
    clip.py        the audio: picks the caption window (text vs position
                   match) and cuts it -- source audio, loopback, fallbacks
    timing.py      window maths: pre/postroll, min/max clip (the appear/clear
                   lags come from cappa.detection — they measure the
                   pipeline, not the card)
    edit.py        the preview's edit engine: a workspace of audio around
                   the clip (cut once, held in memory), a timed word
                   timeline over it, and the commits — re-cut audio.wav,
                   regrow the sentence word by word, type over any text —
                   each recorded under the draft's "edited" provenance
    model.py       CardDraft
    prefs.py       which fields a card collects and on which side (live copy
                   of the Flashcards settings tab)
    template.py    Anki-style card template: HTML faces + CSS, default design
    writer.py      card_NNNN folders + metadata.json (the card's provenance
                   record -- add keys, never rename them); discard_draft
                   deletes a draft the user rejected in the preview
    anki_sync.py   sync(): puts the new card into Anki -- live via the
                   AnkiConnect add-on when Anki is open (visible instantly),
                   straight into its collection file when closed (visible
                   next launch). A per-folder anki_synced.txt receipt means
                   a delivered card is never touched again.

Qt-free; the UI calls build_draft/sync_to_anki from a worker thread. Every
missing piece becomes a draft note, never an exception.

build_draft SAVES the draft but delivers nothing: the preview window
(ui/card_preview.py) shows it and then either calls sync_to_anki or
discard_draft."""

from . import prefs
from .anki_sync import SyncError, sync as sync_to_anki
from .builder import CARDS_DIR, build_draft, capture_png
from .model import CardDraft
from .writer import discard_draft

__all__ = ["CARDS_DIR", "CardDraft", "SyncError", "build_draft",
          "capture_png", "discard_draft", "prefs", "sync_to_anki"]
