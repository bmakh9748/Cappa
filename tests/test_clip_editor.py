"""Unit test for the preview's edit engine (flashcard/edit.py) and the
edit strip's widget contract (ui/clip_editor.py).

No window is shown and no network is touched: a FakeEditSource stands in
for the SourceSession (clip_wav writes a WAV whose sample VALUES encode
absolute time, so cuts are provable sample-accurate) and a FakeRing for
the loopback recorder. What matters here is the editing contract: the
workspace is cut once around the clip, the word timeline carries the
card's own words plus its neighbours, sliding the audio range regrows the
sentence word by word (and a word span maps back to an audio range), and
every commit leaves the draft FOLDER agreeing with the draft -- because
anki_sync reads the folder, not the object."""

import json
import os
import sys
import tempfile
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from cappa.flashcard import edit as edit_mod
from cappa.flashcard.edit import DraftEditor, _join, set_text
from cappa.flashcard.model import CardDraft
from cappa.flashcard import timing
from cappa.source.transcript import Transcript
from cappa.source.vtt import Token

RATE = 100   # 100 samples/s keeps time-encoded int16 values in range


def _time_coded_wav(path, t0, t1):
    """A mono WAV whose sample at absolute time t holds value t*RATE --
    reading any cut back tells you exactly which window it came from."""
    frames = np.arange(int(round(t0 * RATE)), int(round(t1 * RATE)),
                       dtype=np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(frames.tobytes())
    return t1 - t0


def _first_sample(path):
    with wave.open(path, "rb") as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        return int(data[0]), len(data) / w.getframerate()


class FakeEditSource:
    """The slice of SourceSession the editor uses, video timebase."""

    def __init__(self, duration=60.0, words=(), sentence=None):
        self.duration = duration
        self.words = list(words)          # (text, start, end) neighbour pool
        self.sentence = sentence          # sentence_for's canned answer
        self.sliced = None

    def ensure_audio(self):
        return "audio.m4a"

    def meta(self):
        return {"video_id": "x", "duration": self.duration}

    def clip_wav(self, out_path, start, end, preroll=0.0, postroll=0.0):
        self.sliced = (start, end)
        return _time_coded_wav(out_path, start, end)

    def sentence_for(self, text, near_t=None):
        return self.sentence

    def words_between(self, t0, t1):
        return [(w, s, e) for w, s, e in self.words
                if e >= t0 and s <= t1]

    def rows_between(self, t0, t1):
        return []


class FakeRing:
    """The slice of LoopbackRecorder the editor uses, monotonic timebase."""

    ready = True

    def __init__(self, held):
        self.held = held

    def buffered_window(self):
        return self.held

    def clip(self, t0, t1):
        frames = np.arange(int(round(t0 * RATE)), int(round(t1 * RATE)),
                           dtype=np.int16).reshape(-1, 1)
        return frames, RATE


def make_draft(folder, source_kind):
    os.makedirs(folder, exist_ok=True)
    draft = CardDraft("kucing", "tadi kucing saya melahirkan")
    draft.word_translation = "cat"
    draft.sentence_translation = "my cat just gave birth"
    draft.folder_path = folder
    draft.word_index = 1
    draft.audio_path = os.path.join(folder, "audio.wav")
    if source_kind == "source_audio":
        t0, t1 = 30.0, 33.0
    else:
        t0, t1 = 100.0, 103.0
    _time_coded_wav(draft.audio_path, t0, t1)
    draft.audio_seconds = t1 - t0
    draft.audio_window = {"source": source_kind, "start": t0, "end": t1}
    return draft


with tempfile.TemporaryDirectory() as tmp:
    # ---- video mode: workspace is one wide cut around the clip ----------
    src = FakeEditSource(words=[("sebelum", 29.0, 29.4),
                                ("sesudah", 34.0, 34.4)])
    draft = make_draft(os.path.join(tmp, "card_0001"), "source_audio")
    ed = DraftEditor.prepare(draft, source=src)
    assert ed.ready, ed.error
    assert ed.mode == "video"
    assert src.sliced == (10.0, 53.0), src.sliced   # clip 30-33 padded ±20
    assert abs(ed.ws_start - 10.0) < 1e-6 and abs(ed.ws_end - 53.0) < 0.02
    assert not os.path.exists(os.path.join(draft.folder_path,
                                           "edit_workspace.wav"))
    print("PASS: video workspace cut once, clip ±%.0fs, clamped, cleaned"
          % edit_mod.WORKSPACE_PAD)

    # ---- the timeline: neighbours dim around the card's own words ------
    texts = [w["text"] for w in ed.words]
    kinds = [w["kind"] for w in ed.words]
    assert texts == ["sebelum", "tadi", "kucing", "saya", "melahirkan",
                     "sesudah"], texts
    assert kinds == ["neighbor", "core", "core", "core", "core",
                     "neighbor"], kinds
    assert ed.core == (1, 4) and ed.span == (1, 4)
    core_times = [(w["start"], w["end"]) for w in ed.words[1:5]]
    assert abs(core_times[0][0] - 30.0) < 1e-6
    assert abs(core_times[-1][1] - 33.0) < 1e-6
    assert all(b > a for a, b in core_times)
    print("PASS: timeline = neighbours + the card's words spread over the clip")

    # ---- audio range -> word span (midpoint rule), and back -------------
    span = ed.words_in_range(28.8, 33.0)
    assert span == (0, 4), span               # sebelum's midpoint now covered
    assert ed.sentence_text(*span) == "sebelum tadi kucing saya melahirkan"
    t0, t1 = ed.range_for_words(0, 4)
    assert abs(t0 - (29.0 - timing.PREROLL)) < 1e-6, t0
    assert abs(t1 - (33.0 + timing.POSTROLL)) < 1e-6, t1
    assert ed.words_in_range(31.0, 31.2) == (2, 2)   # kucing alone
    print("PASS: range->words by midpoint, words->range with pre/postroll")

    # ---- recut is sample-accurate and rewrites the folder ---------------
    seconds = ed.recut(29.0, 33.4)
    assert abs(seconds - 4.4) < 0.05, seconds
    first, length = _first_sample(draft.audio_path)
    assert first == 2900, first               # the sample AT 29.0s
    assert abs(length - 4.4) < 0.05
    assert draft.audio_window["start"] == 29.0
    assert abs(draft.audio_seconds - 4.4) < 0.05
    meta = json.load(open(os.path.join(draft.folder_path, "metadata.json"),
                          encoding="utf-8"))
    assert meta["edited"]["audio"] == {"start": 29.0, "end": 33.4}
    assert meta["edited"]["original"]["audio_window"]["start"] == 30.0
    assert meta["edited"]["original"]["sentence"] \
        == "tadi kucing saya melahirkan"
    print("PASS: recut cuts the exact window and records the edit + original")

    # ---- the slider path regrows the sentence, word_index follows -------
    changed = ed.set_sentence_span(0, 4)
    assert changed
    assert draft.sentence == "sebelum tadi kucing saya melahirkan"
    assert draft.word_index == 2              # kucing moved one to the right
    text = open(os.path.join(draft.folder_path, "sentence.txt"),
                encoding="utf-8").read()
    assert text == draft.sentence
    meta = json.load(open(os.path.join(draft.folder_path, "metadata.json"),
                          encoding="utf-8"))
    assert meta["edited"]["manual"] == []     # slider-grown, not typed
    print("PASS: span commit regrows the sentence on disk, not as 'manual'")

    # ---- a manual edit is marked manual and never resplit ---------------
    assert set_text(draft, "sentence", "apa kabar dunia", manual=True)
    assert draft.word_index == -1             # kucing no longer in it
    assert not set_text(draft, "sentence", "apa kabar dunia", manual=True)
    meta = json.load(open(os.path.join(draft.folder_path, "metadata.json"),
                          encoding="utf-8"))
    assert meta["edited"]["manual"] == ["sentence"]
    print("PASS: manual edits are recorded as manual; no-ops don't rewrite")

    # ---- a manual WORD edit clears the now-wrong breakdown + TTS ---------
    draft.word = "kucing"
    draft.sentence = "tadi kucing saya melahirkan"
    draft.breakdown = "<b>anatomy of kucing</b>"
    wa = os.path.join(draft.folder_path, "word_audio.mp3")
    open(wa, "wb").write(b"ID3fake")
    draft.word_audio_path = wa
    assert set_text(draft, "word", "melahirkan", manual=True)
    assert draft.breakdown == ""              # described the old word
    assert draft.word_audio_path is None and not os.path.exists(wa)
    assert draft.word_index == 3              # melahirkan is word 3
    bd = open(os.path.join(draft.folder_path, "breakdown.txt"),
              encoding="utf-8").read()
    assert bd == ""
    print("PASS: a manual word edit clears the stale breakdown and TTS audio")

    # ---- CJK: word_index is a char offset, clicked chip found by text ----
    cj = CardDraft("戻る", "戻るのも見た")
    cj.folder_path = os.path.join(tmp, "card_0009")
    os.makedirs(cj.folder_path, exist_ok=True)
    set_text(cj, "sentence", "彼は戻るのも見た", manual=False)
    assert cj.word_index == cj.sentence.find("戻る") == 2   # char offset
    print("PASS: a CJK regrow reindexes by character offset, not split()")

    # ---- write into a discarded (folder=None) draft can't crash/recreate -
    ghost_dir = os.path.join(tmp, "card_0010")
    ghost = CardDraft("x", "y z")
    ghost.folder_path = None
    set_text(ghost, "sentence", "z y", manual=True)   # must not raise
    assert not os.path.exists(ghost_dir)
    print("PASS: a write into a discarded draft can't crash or recreate it")

    # ---- track word timings are adopted when the words align ------------
    sent = {"start": 29.9, "end": 33.1, "score": 0.9,
            "words": [("tadi", 29.9, 30.4), ("kucing", 30.4, 31.0),
                      ("saya", 31.0, 31.6), ("melahirkan", 31.6, 33.1)],
            "match_start": 29.9, "match_end": 33.1}
    src2 = FakeEditSource(sentence=sent)
    draft2 = make_draft(os.path.join(tmp, "card_0002"), "source_audio")
    ed2 = DraftEditor.prepare(draft2, source=src2)
    assert ed2.ready, ed2.error
    kucing = ed2.words[ed2.core[0] + 1]
    assert (kucing["start"], kucing["end"]) == (30.4, 31.0), kucing
    print("PASS: aligned track words lend their exact timings to the core")

    # ---- loopback mode: ring cut, clamped to what the ring holds --------
    ring = FakeRing((90.0, 123.0))
    draft3 = make_draft(os.path.join(tmp, "card_0003"), "loopback_monotonic")
    ed3 = DraftEditor.prepare(draft3, recorder=ring)
    assert ed3.ready, ed3.error
    assert ed3.mode == "monotonic"
    assert abs(ed3.ws_start - 90.0) < 1e-6    # pad hit the ring's left edge
    assert abs(ed3.ws_end - 123.0) < 0.02     # right edge: nothing newer held
    assert [w["kind"] for w in ed3.words] == ["core"] * 4   # no source: no
    seconds = ed3.recut(100.0, 108.0)         # neighbours to offer
    assert abs(seconds - 8.0) < 0.02
    first, _ = _first_sample(draft3.audio_path)
    assert first == 10000, first              # the sample AT monotonic 100.0
    print("PASS: loopback workspace clamps to the ring and recuts exactly")

    # ---- no audio on the card: editing fails soft, with a reason --------
    bare = CardDraft("x", "y z")
    bare.folder_path = os.path.join(tmp, "card_0004")
    os.makedirs(bare.folder_path, exist_ok=True)
    ed4 = DraftEditor.prepare(bare)
    assert not ed4.ready and ed4.error, ed4.error
    print("PASS: a card without audio reports why it can't be edited")

    # ---- selection clamping ---------------------------------------------
    t0, t1 = ed.clamp_selection(0.0, 200.0)
    assert t0 >= ed.ws_start and t1 <= ed.ws_end
    t0, t1 = ed.clamp_selection(20.0, 20.05)
    assert t1 - t0 >= edit_mod.SEL_MIN - 1e-9
    print("PASS: selections stay inside the workspace and above SEL_MIN")

    # ---- envelope: one peak per bin, normalized -------------------------
    env = ed.envelope(64)
    assert len(env) == 64
    assert max(env) == 1.0 and min(env) >= 0.0
    assert ed.envelope(64) is env             # cached per bin count
    print("PASS: waveform envelope is binned, normalized and cached")

# ---- the CJK join rule ---------------------------------------------------
assert _join(["tadi", "kucing"]) == "tadi kucing"
assert _join(["戻る", "の", "も"]) == "戻るのも"
assert _join(["I", "見た"]) == "I 見た"
print("PASS: sentence join puts spaces between words, none inside CJK")

# ---- Transcript.words_between: touching words, markers dropped -----------
tr = Transcript([Token("hello", 1.0, 1.4), Token("&gt;&gt;", 1.4, 1.5),
                 Token("world", 1.5, 2.0), Token("again", 5.0, 5.5)])
got = tr.words_between(1.2, 4.0)
assert [w for w, _, _ in got] == ["hello", "world"], got
assert tr.words_between(3.0, 3.5) == []
print("PASS: words_between returns touching words and drops '>>' markers")

# ---- the edit strip's widget contract (headless, no window shown) --------
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])
from cappa.ui.clip_editor import ClipEditor

with tempfile.TemporaryDirectory() as tmp:
    src = FakeEditSource(words=[("sebelum", 29.0, 29.4)])
    draft = make_draft(os.path.join(tmp, "card_0001"), "source_audio")
    ed = DraftEditor.prepare(draft, source=src)
    strip = ClipEditor()
    strip.attach(ed, ed.clicked_word_index(draft.word))
    assert strip.linked()                     # coupled by default
    assert strip._strip._words is ed.words
    assert strip._strip._clicked == ed.core[0] + 1   # kucing's chip, by text
    assert "3.0" in strip._length.text()      # the clip's length
    fired = []
    strip.spanEdited.connect(lambda i, j, final: fired.append((i, j, final)))
    strip._strip.set_span(0, 4)               # display only, no signal
    assert fired == []
    strip.set_linked(False)
    assert not strip.linked()
    nearest = strip._strip._nearest_word(29.1)
    assert nearest == 0                       # sebelum's midpoint is closest
    # a locked span refuses both handles (no s0/s1 hit)
    strip.lock_span()
    from PySide6.QtCore import QPointF
    from cappa.ui.clip_editor import WAVE_H
    y = WAVE_H + 5
    xa = strip._strip._x(ed.words[ed.span[0]]["start"])
    assert strip._strip._hit(QPointF(xa, y)) is None
    print("PASS: the strip mounts an editor, quantizes to midpoints, locks span")

print("\nALL PASS")
