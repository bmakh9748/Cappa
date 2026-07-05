"""Unit test for cappa/flashcard.py draft-folder creation.

No network, no screen capture, no audio device: translator/screenshot/recorder
are faked so this only checks the draft contract that the popup depends on.
"""

import json
import os
import sys
import tempfile
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cappa.detection.sentence import Sentence, Word
from cappa.flashcard import build_draft


def fake_translate(text, sentence=""):
    return "tx:" + text


def fake_screenshot(region, path):
    assert region == (1, 2, 300, 120)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")


class FakeRecorder:
    ready = True
    error = ""

    def __init__(self):
        self.calls = []

    def save_wav(self, path, t0, t1):
        assert t1 > t0
        self.calls.append((path, t0, t1))
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(1000)
            w.writeframes(b"\x00\x00" * 250)
        return 0.25


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


with tempfile.TemporaryDirectory() as tmp:
    sentence = Sentence(
        "hello world",
        (10, 20, 120, 50),
        [("hello", (10, 20, 55, 50)), ("world", (60, 20, 120, 50))],
    )
    sentence.appeared_at = 100.0
    sentence.cleared_at = 102.0
    rec = FakeRecorder()
    click_png = b"\x89PNG\r\n\x1a\nclicked-frame"

    draft = build_draft(
        sentence.words[1],
        None,
        rec,
        out_dir=tmp,
        translator=fake_translate,
        screenshotter=fake_screenshot,
        screenshot_png=click_png,
    )
    folder = draft.folder_path
    assert os.path.basename(folder) == "card_0001"
    assert os.path.exists(os.path.join(folder, "screenshot.png"))
    with open(os.path.join(folder, "screenshot.png"), "rb") as f:
        assert f.read() == click_png
    assert os.path.exists(os.path.join(folder, "audio.wav"))
    assert read_text(os.path.join(folder, "word.txt")) == "world"
    assert read_text(os.path.join(folder, "sentence.txt")) == "hello world"
    assert read_text(os.path.join(folder, "word_translation.txt")) == "tx:world"
    assert read_text(os.path.join(folder, "sentence_translation.txt")) == (
        "tx:hello world"
    )

    with open(os.path.join(folder, "metadata.json"), encoding="utf-8") as f:
        meta = json.load(f)
    assert meta["sentence_verified"] is True
    assert meta["word_index"] == 1
    assert meta["word_box"] == [60, 20, 120, 50]
    assert meta["sentence_box"] == [10, 20, 120, 50]
    assert meta["screenshot"] == "screenshot.png"
    assert meta["screenshot_source"] == "word_click"
    assert meta["audio"] == "audio.wav"
    assert meta["audio_seconds"] == 0.25
    assert meta["audio_window"]["source"] == "loopback_monotonic"
    assert meta["notes"] == []
    assert len(rec.calls) == 1
    print("PASS: verified clicked word creates a complete draft folder")

    draft2 = build_draft(
        sentence.words[0],
        None,
        None,
        out_dir=tmp,
        translator=fake_translate,
        screenshotter=fake_screenshot,
    )
    assert os.path.basename(draft2.folder_path) == "card_0002"
    assert os.path.exists(os.path.join(draft2.folder_path, "metadata.json"))
    assert not os.path.exists(os.path.join(draft2.folder_path, "screenshot.png"))
    assert not os.path.exists(os.path.join(draft2.folder_path, "audio.wav"))
    with open(os.path.join(draft2.folder_path, "metadata.json"),
              encoding="utf-8") as f:
        meta2 = json.load(f)
    assert "no tracked area for a screenshot" in meta2["notes"]
    assert "audio recorder not running" in meta2["notes"]
    print("PASS: missing screenshot/audio still writes a partial draft")

    orphan = Word("ghost", (200, 200, 240, 230), sentence)
    draft3 = build_draft(
        orphan,
        None,
        None,
        out_dir=tmp,
        translator=fake_translate,
        screenshotter=fake_screenshot,
    )
    with open(os.path.join(draft3.folder_path, "metadata.json"),
              encoding="utf-8") as f:
        meta3 = json.load(f)
    assert meta3["sentence_verified"] is False
    assert "clicked word is not in its sentence word list" in meta3["notes"]
    assert "clicked word box is outside the sentence box" in meta3["notes"]
    assert "clicked word text not found in OCR sentence" in meta3["notes"]
    print("PASS: mismatched word/sentence provenance is flagged")

print("ALL PASS")
