"""App settings: a tiny persisted holder.

Basic for now -- just the language clicked words are translated into. This is
the home for future settings: add a field here (with a default and validation in
load()) and a row in cappa.ui.startup. Persists to settings.json in the project
root; a missing/corrupt file just yields defaults. No Qt."""

import json
import os

_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "settings.json")

# Languages offered in the pickers: (Google/deep-translator code, display name).
# Trim or extend freely; codes must be ones GoogleTranslator accepts.
_LANGUAGES = [
    ("en", "English"),
    ("ar", "Arabic"),
    ("es", "Spanish"),
    ("fr", "French"),
    ("de", "German"),
    ("it", "Italian"),
    ("pt", "Portuguese"),
    ("ru", "Russian"),
    ("tr", "Turkish"),
    ("id", "Indonesian"),
    ("ja", "Japanese"),
    ("ko", "Korean"),
    ("zh-CN", "Chinese (Simplified)"),
    ("hi", "Hindi"),
]

# The language clicked words are translated INTO (the user's language).
TARGET_LANGUAGES = list(_LANGUAGES)
DEFAULT_TARGET = "en"

# The language the video is IN (what the user is learning). "auto" keeps the
# old per-word auto-detect, which fails on lone words -- naming it fixes that.
SOURCE_LANGUAGES = [("auto", "Auto-detect")] + list(_LANGUAGES)
DEFAULT_SOURCE = "auto"

_TARGET_CODES = {code for code, _ in TARGET_LANGUAGES}
_SOURCE_CODES = {code for code, _ in SOURCE_LANGUAGES}


class Settings:
    def __init__(self, target_language=DEFAULT_TARGET,
                 source_language=DEFAULT_SOURCE):
        self.target_language = target_language
        self.source_language = source_language

    def to_dict(self):
        return {
            "target_language": self.target_language,
            "source_language": self.source_language,
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
    return Settings(target, source)


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
