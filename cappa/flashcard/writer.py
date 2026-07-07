"""Disk layout for card draft folders."""

import json
import os

from . import prefs

CARDS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "cards",
)


def next_card_dir(out_dir):
    """Create and return the next cards/card_0001-style draft folder."""
    os.makedirs(out_dir, exist_ok=True)
    i = 1
    while True:
        path = os.path.join(out_dir, "card_%04d" % i)
        try:
            os.mkdir(path)
            return path
        except FileExistsError:
            i += 1


def write_artifacts(draft):
    folder = draft.folder_path
    _write_text(os.path.join(folder, "word.txt"), draft.word)
    _write_text(os.path.join(folder, "sentence.txt"), draft.sentence)
    _write_text(os.path.join(folder, "word_translation.txt"),
                draft.word_translation)
    _write_text(os.path.join(folder, "sentence_translation.txt"),
                draft.sentence_translation)
    if draft.notes:
        _write_text(os.path.join(folder, "notes.txt"), "\n".join(draft.notes))

    draft.metadata_path = os.path.join(folder, "metadata.json")
    with open(draft.metadata_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(_metadata(draft, folder), f, ensure_ascii=False, indent=2)
        f.write("\n")


def _write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text or "")


def _relpath(path, root):
    if not path:
        return None
    return os.path.relpath(path, root).replace(os.sep, "/")


def _metadata(draft, folder):
    return {
        "created_at": draft.created_at,
        "word": draft.word,
        "sentence": draft.sentence,
        "word_translation": draft.word_translation,
        "sentence_translation": draft.sentence_translation,
        "sentence_verified": draft.sentence_verified,
        "word_index": draft.word_index,
        "word_box": list(draft.word_box) if draft.word_box else None,
        "sentence_box": (
            list(draft.sentence_box) if draft.sentence_box else None
        ),
        "appeared_at_monotonic": draft.appeared_at,
        "cleared_at_monotonic": draft.cleared_at,
        "screenshot": _relpath(draft.image_path, folder),
        "screenshot_source": draft.screenshot_source,
        "audio": _relpath(draft.audio_path, folder),
        "audio_seconds": draft.audio_seconds,
        "audio_window": draft.audio_window,
        "video_source": draft.source_meta,
        # The front/back layout and Anki-style template configured when this
        # card was made, so the future .apkg export renders it the way the
        # user had it set then.
        "card_layout": prefs.layout(),
        "card_template": prefs.template(),
        "notes": list(draft.notes),
    }
