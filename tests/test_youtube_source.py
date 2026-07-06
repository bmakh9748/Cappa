"""Unit test: VTT parsing + OCR-line alignment, against real YouTube fixtures.

Windowless and network-free -- it reads the two .vtt files captured under
tests/fixtures (one uploader/manual track, one auto-generated rolling track)
and checks that parsing yields clean timed words and that a mangled, upper-cased
"OCR" line aligns back to the correct caption window."""

import json
import os
import sys
import tempfile
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cappa.detection.sentence import Sentence
from cappa.flashcard import build_draft
from cappa.source import Transcript, parse_vtt

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _load(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


def test_manual():
    tokens = parse_vtt(_load("manual.id.vtt"))
    assert tokens, "no tokens from manual VTT"
    # Times are sane: sorted, non-negative, end > start.
    for a, b in zip(tokens, tokens[1:]):
        assert a.start <= b.start + 1e-6, "tokens out of order"
    for t in tokens:
        assert t.end > t.start >= 0.0, "bad token window %r" % (t,)

    text = " ".join(t.text for t in tokens)
    assert "peradaban" in text.casefold(), "manual text missing: %r" % text[:80]

    # A line's real cue is 00:00:12.040 --> 00:00:15.680. An OCR read of it,
    # upper-cased with a typo, must align back onto that window.
    tr = Transcript(tokens)
    m = tr.window_for("DARI PERTANYAAN MUNCULAH PEMIKIRAN")  # 'pemikiran' cue
    assert m, "manual line did not align"
    assert abs(m["start"] - 12.040) < 0.5, "start off: %.3f" % m["start"]
    assert abs(m["end"] - 15.680) < 0.5, "end off: %.3f" % m["end"]
    print("PASS manual: %d tokens; aligned window %.2f-%.2f (score %.2f) %r"
          % (len(tokens), m["start"], m["end"], m["score"], m["text"]))


def test_auto():
    tokens = parse_vtt(_load("auto.id.vtt"))
    assert tokens, "no tokens from auto VTT"

    words = [t.text for t in tokens]
    # De-dup worked: the rolling format shows each settled line many times, but
    # a distinctive word must survive exactly once.
    assert words.count("smartw") == 1, (
        "expected 'smartw' once, got %d" % words.count("smartw"))
    # Bracket noise like [Musik] is dropped; the single-word line 'anjing'
    # (which carried no inline timing) is still captured.
    assert "Musik" not in words and "[Musik]" not in words, "bracket noise kept"
    assert "anjing" in words, "novel tag-less line 'anjing' was lost"

    # Inline per-word timings are used: 'clod' is tagged at 00:00:01.920.
    clod = next(t for t in tokens if t.text == "clod")
    assert abs(clod.start - 1.920) < 0.05, "clod start off: %.3f" % clod.start

    # Align an OCR-ish read of the first spoken line to its window.
    tr = Transcript(tokens)
    m = tr.window_for("dari clod leich kalau pengin smartw ris")
    assert m, "auto line did not align"
    assert abs(m["start"] - 1.560) < 0.6, "auto start off: %.3f" % m["start"]
    print("PASS auto: %d tokens; aligned window %.2f-%.2f (score %.2f) %r"
          % (len(tokens), m["start"], m["end"], m["score"], m["text"]))


def test_no_match():
    tokens = parse_vtt(_load("manual.id.vtt"))
    tr = Transcript(tokens)
    assert tr.window_for("completely unrelated english sentence here") is None, (
        "unrelated text should not align")
    print("PASS no-match: unrelated OCR text reports no window")


class FakeSource:
    """Stands in for SourceSession in the builder tests: a canned text match, a
    canned position window, a video->clock mapping, and a clip writer, so the
    card path is exercised without network or ffmpeg."""

    transcript_ready = True

    def __init__(self, match, meta, pos=None, mono=None, clip_fails=False):
        self._match = match
        self._meta = meta
        self._pos = pos
        self._mono = mono          # canned (t0, t1) monotonic window, or None
        self._clip_fails = clip_fails
        self.sliced = None

    def window_for(self, text, near_t=None):
        return self._match

    def window_at(self, t):
        return self._pos

    def monotonic_window(self, start, end):
        return self._mono

    def meta(self):
        return dict(self._meta)

    def clip_wav(self, out_path, start, end, preroll=0.0, postroll=0.0):
        if self._clip_fails:
            raise RuntimeError("audio not downloaded yet")
        self.sliced = (start, end, preroll, postroll)
        with wave.open(out_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(1000)
            w.writeframes(b"\x00\x00" * 500)
        return 0.5


def test_builder_prefers_caption_track():
    with tempfile.TemporaryDirectory() as tmp:
        sentence = Sentence(
            "dari pemikiran muncullah kemajuan", (10, 20, 300, 50),
            [("dari", (10, 20, 50, 50)), ("kemajuan", (60, 20, 300, 50))])
        match = {"start": 16.44, "end": 19.86, "score": 0.96, "i": 0, "j": 4,
                 "text": "Dari pemikiran, muncullah kemajuan."}
        meta = {"video_id": "abc", "url": "u", "title": "t", "channel": "c",
                "caption_lang": "id", "caption_auto": False}
        src = FakeSource(match, meta)
        # recorder=None on purpose: if the source path didn't win, the fallback
        # would leave an "audio recorder not running" note we can detect.
        draft = build_draft(sentence.words[0], None, None, out_dir=tmp,
                            translator=lambda t, s="": "tx:" + t,
                            screenshot_note="no shot", source=src)
        assert src.sliced is not None, "source clip was never cut"
        assert draft.audio_path and os.path.exists(draft.audio_path)
        with open(os.path.join(draft.folder_path, "metadata.json"),
                  encoding="utf-8") as f:
            m = json.load(f)
        assert m["audio_window"]["source"] == "caption_track", m["audio_window"]
        assert m["audio_window"]["matched_by"] == "text", m["audio_window"]
        assert abs(m["audio_window"]["start"] - 16.44) < 1e-6
        assert m["video_source"]["caption_lang"] == "id"
        assert m["video_source"]["caption_auto"] is False
        assert not any("recorder" in n for n in m["notes"]), m["notes"]
        print("PASS builder: caption-track audio preferred (window 16.44-19.86)")


def test_window_for_near():
    """near_t confines the search: a line is found when we look near its time
    and excluded when we look elsewhere in the video."""
    tokens = parse_vtt(_load("manual.id.vtt"))
    tr = Transcript(tokens)
    m = tr.window_for("DARI PERTANYAAN MUNCULAH PEMIKIRAN", near_t=13.0)
    assert m and abs(m["start"] - 12.04) < 0.5, m
    assert tr.window_for("DARI PERTANYAAN MUNCULAH PEMIKIRAN",
                         near_t=300.0) is None, "far search should exclude it"
    print("PASS near: found near 13s (%.2f-%.2f); excluded near 300s"
          % (m["start"], m["end"]))


def test_window_for_rejects_shared_tail():
    """The card-5 failure: an OCR line must NOT match a far caption that only
    shares a trailing word or two -- neither globally (word-blended metric) nor
    when we're actually elsewhere in the video (position constraint)."""
    from cappa.source.vtt import Token
    toks = []

    def add(words, t0):
        for k, w in enumerate(words):
            toks.append(Token(w, t0 + k * 0.4, t0 + k * 0.4 + 0.4))

    add(["aku", "kira", "ini", "punyamu"], 30.0)   # real spot, garbled words
    add(["duluan", "lagi", "nih"], 272.0)          # decoy far away, shared tail
    tr = Transcript(toks)
    assert tr.window_for("ku bawa lagi nih") is None, "decoy matched globally"
    assert tr.window_for("ku bawa lagi nih", near_t=30.0) is None, (
        "decoy should be out of range near 30s")
    w = tr.window_at(30.0)
    assert w and abs(w["start"] - 30.0) < 0.5, w
    print("PASS shared-tail: decoy never matched; position pins the real 30.0s")


def test_window_at():
    tokens = parse_vtt(_load("manual.id.vtt"))
    tr = Transcript(tokens)
    w = tr.window_at(13.0)   # inside "Dari pertanyaan, munculah pemikiran."
    assert w and w["by"] == "position", w
    assert w["start"] <= 13.0 <= w["end"], w
    assert (w["end"] - w["start"]) < 8.0, w
    assert "pemikiran" in w["text"].casefold(), w["text"]
    print("PASS window_at: pos 13.0 -> %.2f-%.2f %r"
          % (w["start"], w["end"], w["text"]))


def test_builder_position_fallback():
    """OCR text that isn't in the caption track (a translated burned-in sub, or
    a garbled auto-caption at the real spot) must fall back to the playback
    position: window_at at click-time near_t."""
    with tempfile.TemporaryDirectory() as tmp:
        sentence = Sentence(
            "english translation not in the track", (0, 0, 10, 10),
            [("english", (0, 0, 5, 5)), ("track", (6, 0, 10, 10))])
        pos = {"start": 40.0, "end": 43.5, "score": 0.0,
               "text": "spoken words", "by": "position"}
        src = FakeSource(None, {"video_id": "vid", "caption_lang": "ja",
                                "caption_auto": True}, pos=pos)
        draft = build_draft(sentence.words[0], None, None, out_dir=tmp,
                            translator=lambda t, s="": "tx:" + t,
                            screenshot_note="no shot", source=src, near_t=42.0)
        assert src.sliced is not None, "position window was not used"
        with open(os.path.join(draft.folder_path, "metadata.json"),
                  encoding="utf-8") as f:
            m = json.load(f)
        assert m["audio_window"]["source"] == "caption_track"
        assert m["audio_window"]["matched_by"] == "position", m["audio_window"]
        assert abs(m["audio_window"]["start"] - 40.0) < 1e-6
        assert m["video_source"]["matched_by"] == "position"
        print("PASS builder: position window used when text doesn't match")


def test_builder_loopback_rescue():
    """Source audio unavailable (bot check / still downloading) but the caption
    window is known: the clip must be cut from the LOOPBACK buffer at the
    mapped clock times, not silently dropped."""

    class FakeRecorder:
        ready = True
        error = ""

        def __init__(self):
            self.cut = None

        def save_wav(self, path, t0, t1):
            self.cut = (t0, t1)
            with wave.open(path, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(1000)
                w.writeframes(b"\x00\x00" * 800)
            return 0.8

    with tempfile.TemporaryDirectory() as tmp:
        sentence = Sentence(
            "ku bawa lagi nih", (0, 0, 10, 10),
            [("ku", (0, 0, 5, 5)), ("bawa", (6, 0, 10, 10))])
        pos = {"start": 41.0, "end": 43.0, "score": 0.0,
               "text": "spoken words", "by": "position"}
        src = FakeSource(None, {"video_id": "vid"}, pos=pos,
                         mono=(700010.0, 700012.0), clip_fails=True)
        rec = FakeRecorder()
        draft = build_draft(sentence.words[1], None, rec, out_dir=tmp,
                            translator=lambda t, s="": "tx:" + t,
                            screenshot_note="no shot", source=src, near_t=42.0)
        assert rec.cut is not None, "loopback rescue never cut"
        assert abs(rec.cut[0] - (700010.0 - 0.15)) < 1e-6, rec.cut
        with open(os.path.join(draft.folder_path, "metadata.json"),
                  encoding="utf-8") as f:
            m = json.load(f)
        assert m["audio_window"]["source"] == "loopback_caption_timed", (
            m["audio_window"])
        assert m["audio_window"]["matched_by"] == "position"
        assert any("source audio unavailable" in n for n in m["notes"]), (
            m["notes"])
        assert any("loopback with caption timing" in n for n in m["notes"])
        print("PASS builder: loopback rescue cuts caption-timed clock window")


def test_pick_subtitle_orig():
    """Auto captions must pick the spoken-language track, never an arbitrary
    translation (the 'ab'/Abkhazian regression)."""
    from cappa.source.youtube import SourceError, _pick_subtitle
    fmts = lambda u: [{"ext": "vtt", "url": u}]
    info = {"language": "id", "subtitles": {},
            "automatic_captions": {"ab": fmts("AB"), "id": fmts("ID"),
                                   "id-orig": fmts("ID-ORIG")}}
    code, is_auto, url = _pick_subtitle(info, None, True)
    assert code == "id" and is_auto and url == "ID", (code, url)

    # No language field, no requested lang: the -orig variant wins, never 'ab'.
    info2 = {"language": None, "subtitles": {},
             "automatic_captions": {"ab": fmts("AB"),
                                    "ja-orig": fmts("JA-ORIG")}}
    code2, _, url2 = _pick_subtitle(info2, None, True)
    assert code2 == "ja-orig" and url2 == "JA-ORIG", (code2, url2)

    # Only translations available: refuse rather than pick garbage.
    info3 = {"language": None, "subtitles": {},
             "automatic_captions": {"ab": fmts("AB"), "de": fmts("DE")}}
    try:
        _pick_subtitle(info3, None, True)
        raise AssertionError("translated-only pool should raise")
    except SourceError:
        pass
    print("PASS pick_subtitle: spoken language / -orig only, 'ab' never wins")


def test_choose_window():
    """The decision core: position governs when a text match is far from where
    we are, or weak; a strong, nearby text match is trusted for its precision."""
    from cappa.flashcard.builder import _choose_window
    strong_near = {"start": 30.0, "end": 33.0, "score": 0.90, "by": "text"}
    strong_far = {"start": 60.0, "end": 63.0, "score": 0.90, "by": "text"}
    weak_near = {"start": 30.0, "end": 33.0, "score": 0.66, "by": "text"}
    pos = {"start": 29.5, "end": 33.5, "score": 0.0, "by": "position"}
    assert _choose_window(strong_far, None, None) is strong_far   # no position
    assert _choose_window(strong_near, pos, 31.0) is strong_near  # strong+near
    assert _choose_window(strong_far, pos, 31.0) is pos           # strong but far
    assert _choose_window(weak_near, pos, 31.0) is pos            # weak -> position
    assert _choose_window(weak_near, None, 31.0) is weak_near     # weak, no pos
    print("PASS choose_window: position governs unless text is strong AND near")


def test_builder_falls_back_when_no_match():
    with tempfile.TemporaryDirectory() as tmp:
        sentence = Sentence(
            "something not in the track", (0, 0, 10, 10),
            [("something", (0, 0, 5, 5)), ("track", (6, 0, 10, 10))])
        src = FakeSource(None, {})   # window_for -> None: line isn't in track
        draft = build_draft(sentence.words[0], None, None, out_dir=tmp,
                            translator=lambda t, s="": "tx:" + t,
                            screenshot_note="no shot", source=src)
        assert src.sliced is None, "should not have cut a clip on no match"
        with open(os.path.join(draft.folder_path, "metadata.json"),
                  encoding="utf-8") as f:
            m = json.load(f)
        assert m["video_source"] is None, "no match must not set video_source"
        assert m["audio_window"] is None, m["audio_window"]
        assert any("recorder" in n for n in m["notes"]), m["notes"]
        print("PASS builder: no caption match -> loopback fallback path")


if __name__ == "__main__":
    test_manual()
    test_auto()
    test_no_match()
    test_window_at()
    test_window_for_near()
    test_window_for_rejects_shared_tail()
    test_builder_prefers_caption_track()
    test_builder_position_fallback()
    test_builder_loopback_rescue()
    test_pick_subtitle_orig()
    test_choose_window()
    test_builder_falls_back_when_no_match()
    print("ALL PASS")
