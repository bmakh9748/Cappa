"""App settings: a tiny persisted holder.

Languages, card audio clip bounds, and what goes on a flashcard. This is the
home for future settings: add a field here (with a default and validation in
load()) and a row in cappa.ui.startup. Persists to settings.json in the project
root; a missing/corrupt file just yields defaults. No Qt."""

import json
import os

_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "settings.json")

# Languages offered in the pickers: (Google/deep-translator code, display
# name). Deliberately small: English-only teaching, and just the three
# studied source languages so each gets real depth; no "auto" — every
# capability needs a named language.
TARGET_LANGUAGES = [("en", "English")]
DEFAULT_TARGET = "en"

# The language the video is IN (what the user is learning).
SOURCE_LANGUAGES = [
    ("ar", "Arabic"),
    ("id", "Indonesian"),
    ("ja", "Japanese"),
]
DEFAULT_SOURCE = "id"   # the language the user studies most right now

_TARGET_CODES = {code for code, _ in TARGET_LANGUAGES}
_SOURCE_CODES = {code for code, _ in SOURCE_LANGUAGES}

# Card audio clip length bounds (seconds), user-tunable in the settings
# panel. The minimum is how much a one-word blip caption is widened to; the
# maximum caps any clip however long the caption ran. Auto (the default —
# no length knowledge needed) ignores the maximum and lets the clip fit
# the whole sentence, up to timing.AUTO_MAX_CLIP.
MIN_CLIP_RANGE = (0.5, 1.5)
MAX_CLIP_RANGE = (1.5, 8.0)   # a real spoken sentence: median 2.1s, p90 7.0s
DEFAULT_MIN_CLIP = 1.0
DEFAULT_MAX_CLIP = 3.0
DEFAULT_AUTO_CLIP = True

# What goes on a flashcard, and on which side. Each field is placed on the
# card's front, its back, or turned off; "off" means the piece is not
# gathered at all (no screenshot capture, no audio cut, no translation
# call). (key, label, default placement) in the order the settings rows and
# a future card render use. cappa.flashcard.prefs holds the live copy the
# card builder reads.
CARD_FRONT = "front"
CARD_BACK = "back"
CARD_OFF = "off"
CARD_PLACEMENTS = (CARD_FRONT, CARD_BACK, CARD_OFF)

CARD_FIELDS = [
    ("word", "Word", CARD_FRONT),
    ("word_translation", "Word translation", CARD_BACK),
    ("sentence", "Sentence", CARD_FRONT),
    ("sentence_translation", "Sentence translation", CARD_BACK),
    ("screenshot", "Screenshot", CARD_FRONT),
    ("audio", "Audio", CARD_FRONT),
]
DEFAULT_CARD_FIELDS = {key: default for key, _, default in CARD_FIELDS}


def valid_card_fields(value):
    """Sanitize a card-field mapping: unknown keys are dropped, missing or
    invalid placements get their defaults. Any field may be off -- even the
    clicked word (e.g. a listening card that shows only audio + sentence)."""
    fields = dict(DEFAULT_CARD_FIELDS)
    if isinstance(value, dict):
        for key in fields:
            v = value.get(key)
            if v in CARD_PLACEMENTS:
                fields[key] = v
    return fields


def valid_card_template(value):
    """A custom card template is a {"front", "back", "css"} dict of strings
    (Anki-style HTML + CSS); None for anything malformed or entirely blank.
    A stored template is only USED while use_custom_template is on --
    otherwise the default design is regenerated from the field placements
    whenever they change (cappa.flashcard.template) and the custom one just
    stays saved, ready to switch back to."""
    if not isinstance(value, dict):
        return None
    parts = {k: value.get(k) for k in ("front", "back", "css")}
    if not all(isinstance(v, str) for v in parts.values()):
        return None
    if not any(v.strip() for v in parts.values()):
        return None
    return parts


def _bounded(value, lo, hi, default):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(v, lo), hi)


class Settings:
    def __init__(self, target_language=DEFAULT_TARGET,
                 source_language=DEFAULT_SOURCE,
                 min_clip_seconds=DEFAULT_MIN_CLIP,
                 max_clip_seconds=DEFAULT_MAX_CLIP,
                 card_fields=None, card_template=None,
                 use_custom_template=False, auto_clip=DEFAULT_AUTO_CLIP):
        self.target_language = target_language
        self.source_language = source_language
        self.min_clip_seconds = min_clip_seconds
        self.max_clip_seconds = max_clip_seconds
        self.auto_clip = bool(auto_clip)
        self.card_fields = valid_card_fields(card_fields)
        self.card_template = valid_card_template(card_template)
        # Only meaningful with a design to use.
        self.use_custom_template = (bool(use_custom_template)
                                    and self.card_template is not None)

    def to_dict(self):
        return {
            "target_language": self.target_language,
            "source_language": self.source_language,
            "min_clip_seconds": self.min_clip_seconds,
            "max_clip_seconds": self.max_clip_seconds,
            "auto_clip": self.auto_clip,
            "card_fields": self.card_fields,
            "card_template": self.card_template,
            "use_custom_template": self.use_custom_template,
        }


def load():
    """Read settings.json, falling back to defaults on any problem."""
    try:
        with open(_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return Settings()
    target = data.get("target_language", DEFAULT_TARGET)
    if target not in _TARGET_CODES:
        target = DEFAULT_TARGET
    source = data.get("source_language", DEFAULT_SOURCE)
    if source not in _SOURCE_CODES:
        source = DEFAULT_SOURCE
    min_clip = _bounded(data.get("min_clip_seconds"),
                        *MIN_CLIP_RANGE, default=DEFAULT_MIN_CLIP)
    max_clip = _bounded(data.get("max_clip_seconds"),
                        *MAX_CLIP_RANGE, default=DEFAULT_MAX_CLIP)
    return Settings(target, source, min_clip, max_clip,
                    data.get("card_fields"), data.get("card_template"),
                    data.get("use_custom_template", False),
                    data.get("auto_clip", DEFAULT_AUTO_CLIP))


def save(settings):
    """Persist settings; failures are swallowed (settings are non-critical)."""
    try:
        with open(_PATH, "w", encoding="utf-8", newline="\n") as f:
            json.dump(settings.to_dict(), f, indent=2)
            f.write("\n")
    except OSError:
        pass


def caption_lang(source_code):
    """The caption-fetch language for a chosen video language: None for
    'auto' (let yt-dlp/the detector pick), else the code itself."""
    return None if source_code == "auto" else source_code
