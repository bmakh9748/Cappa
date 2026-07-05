"""Card draft data model."""

import os
import time


class CardDraft:
    """The gathered ingredients of one card draft."""

    __slots__ = (
        "word", "word_translation", "sentence", "sentence_translation",
        "folder_path", "metadata_path", "image_path", "audio_path",
        "audio_seconds", "audio_window", "screenshot_source", "word_box",
        "sentence_box", "word_index", "sentence_verified", "appeared_at",
        "cleared_at", "created_at", "notes",
    )

    def __init__(self, word, sentence):
        self.word = word
        self.word_translation = ""
        self.sentence = sentence
        self.sentence_translation = ""
        self.folder_path = None
        self.metadata_path = None
        self.image_path = None
        self.audio_path = None
        self.audio_seconds = 0.0
        self.audio_window = None
        self.screenshot_source = None
        self.word_box = None
        self.sentence_box = None
        self.word_index = -1
        self.sentence_verified = False
        self.appeared_at = 0.0
        self.cleared_at = 0.0
        self.created_at = time.time()
        self.notes = []

    def summary(self):
        parts = [
            "word=%r -> %r" % (self.word, self.word_translation or "-"),
            "sentence=%r -> %r" % (
                self.sentence, self.sentence_translation or "-"),
            "folder=%s" % (
                os.path.basename(self.folder_path)
                if self.folder_path else "none"),
            "image=%s" % (
                os.path.basename(self.image_path) if self.image_path
                else "none"),
            "audio=%s (%.2fs)" % (
                os.path.basename(self.audio_path) if self.audio_path
                else "none", self.audio_seconds),
        ]
        if self.notes:
            parts.append("notes: " + "; ".join(self.notes))
        return " | ".join(parts)
