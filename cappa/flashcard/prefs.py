"""The live copy of the card content settings the builder reads.

Each card field (word, translations, sentence, screenshot, audio) carries a
placement: the card's front, its back, or off. Placements are module state
read at build time (like timing's clip bounds), so a settings change takes
effect on the next card. "off" means the piece is skipped entirely -- no
screenshot capture, no audio cut, no translation call. The sentence TEXT is
the one exception: it is always gathered because caption-track audio
matching and word-translation context need it; off only keeps it off the
card itself.

The card template rides along: None means auto -- regenerate the default
design from the placements on every read, so it follows the settings --
while a custom template saved in the advanced editor is returned verbatim.
The canonical field list, defaults and validation live in cappa.settings."""

from ..settings import (CARD_BACK, CARD_FIELDS, CARD_FRONT, CARD_OFF,
                        DEFAULT_CARD_FIELDS, valid_card_fields,
                        valid_card_template)
from .template import default_template

_placements = dict(DEFAULT_CARD_FIELDS)
_template = None   # custom {"front", "back", "css"} or None = auto


def set_card_fields(mapping):
    """Apply the user's placements process-wide; the next card obeys them."""
    global _placements
    _placements = valid_card_fields(mapping)


def set_card_template(value):
    """Apply the user's custom template (None reverts to the auto default)."""
    global _template
    _template = valid_card_template(value)


def include(field):
    """Whether this piece should be gathered for the card at all."""
    return _placements.get(field, CARD_OFF) != CARD_OFF


def layout():
    """The configured card faces, {"front": [...], "back": [...]} in display
    order -- written into each card's metadata as provenance of how the
    card was configured when it was made."""
    return {
        side: [key for key, _, _ in CARD_FIELDS if _placements[key] == side]
        for side in (CARD_FRONT, CARD_BACK)
    }


def template():
    """The effective card template: the custom design verbatim when one is
    saved, else the default generated from the current placements."""
    return dict(_template) if _template else default_template(_placements)
