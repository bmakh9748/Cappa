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

    def __init__(self, match, meta, pos=None, mono=None, clip_fails=False,
                 appear_t=None, clear_t=None, paused=None, steady=None,
                 sighting=None):
        self._match = match
        self._meta = meta
        self._pos = pos
        self._mono = mono          # canned (t0, t1) monotonic window, or None
        self._clip_fails = clip_fails
        self._appear_t = appear_t  # canned appearance video-time, or None
        self._clear_t = clear_t    # canned clear video-time, or None
        self._paused = paused      # canned bridge paused state (None=unknown)
        self._steady = steady      # canned steady-playback answer (None=unknown)
        self._sighting = sighting  # canned previous-sighting window, or None
        self.sliced = None

    def is_paused(self):
        return self._paused

    def steady_at(self, mono):
        return self._steady

    def sighting_window(self, text, near_t=None):
        return self._sighting

    def video_time_at(self, mono):
        # Tests stamp appearances near mono 100 and clears well after:
        # a mono past 101 is the clear stamp's mapping.
        if self._clear_t is not None and mono > 101.0:
            return self._clear_t
        return self._appear_t

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


def test_prune_cache():
    """The media cache trims itself to its byte cap, oldest first, keeping
    cookies.txt (credentials, not media) whatever its age."""
    from cappa.source.youtube import prune_cache
    with tempfile.TemporaryDirectory() as tmp:
        for i, name in enumerate(["old.webm", "mid.webm", "new.webm",
                                  "cookies.txt"]):
            p = os.path.join(tmp, name)
            with open(p, "wb") as f:
                f.write(b"x" * 100)
            os.utime(p, (1000 + i, 1000 + i))
        removed = prune_cache(cache_dir=tmp, max_bytes=250)
        left = sorted(os.listdir(tmp))
        assert removed == 1 and left == ["cookies.txt", "mid.webm",
                                         "new.webm"], (removed, left)
        assert prune_cache(cache_dir=tmp, max_bytes=250) == 0  # already fits
        assert prune_cache(cache_dir=os.path.join(tmp, "missing")) == 0
        print("PASS prune: cache trimmed oldest-first to the cap, "
              "cookies kept")


def test_builder_block_life_times_the_clip():
    """THE window rule (user spec, 2026-07-09): with no track match, the clip
    is purely the block's on-screen life — earliest appearance to latest
    clear, no buffer (the stamps are already detector-lag-corrected)."""
    with tempfile.TemporaryDirectory() as tmp:
        sentence = Sentence(
            "dari pemikiran muncullah kemajuan", (10, 20, 300, 50),
            [("dari", (10, 20, 50, 50)), ("kemajuan", (60, 20, 300, 50))])
        sentence.appeared_at = 100.0    # -> video 16.5 (fake mapping)
        sentence.cleared_at = 106.0     # -> video 18.8
        meta = {"video_id": "abc", "url": "u", "title": "t", "channel": "c",
                "caption_lang": "id", "caption_auto": False}
        src = FakeSource(None, meta, appear_t=16.5, clear_t=18.8)
        # recorder=None on purpose: if the source path didn't win, the fallback
        # would leave an "audio recorder not running" note we can detect.
        draft = build_draft(sentence.words[0], None, None, out_dir=tmp,
                            translator=lambda t, s="": "tx:" + t,
                            screenshot_note="no shot", source=src)
        assert src.sliced is not None, "source clip was never cut"
        assert draft.audio_path and os.path.exists(draft.audio_path)
        start, end, _, _ = src.sliced
        assert abs(start - 16.5) < 1e-6, src.sliced   # no head buffer
        assert abs(end - 18.8) < 1e-6, src.sliced      # no tail trim
        with open(os.path.join(draft.folder_path, "metadata.json"),
                  encoding="utf-8") as f:
            m = json.load(f)
        assert m["audio_window"]["source"] == "source_audio", m["audio_window"]
        assert m["audio_window"]["matched_by"] == "block_life", m["audio_window"]
        assert m["video_source"]["start_seconds"] == 16.5
        assert m["video_source"]["start_from"] == "onscreen"
        assert m["video_source"]["end_seconds"] == 18.8
        assert m["video_source"]["end_from"] == "onscreen"
        assert m["video_source"]["caption_lang"] == "id"
        assert not any("recorder" in n for n in m["notes"]), m["notes"]
        print("PASS builder: pure on-screen life times the clip (no track)")


def test_builder_clip_end_clamped_to_duration():
    """Nothing past the video's end can play: a clear stamp (or predicted
    end) overrunning the file is empty air on the downloaded audio and, on
    a looping Short, maps the loopback rescue into the NEXT pass. The
    session's known duration caps the window, last (PLAN item 5)."""
    with tempfile.TemporaryDirectory() as tmp:
        sentence = Sentence(
            "sampai jumpa di video berikutnya", (10, 20, 300, 50),
            [("jumpa", (10, 20, 50, 50))])
        sentence.appeared_at = 100.0    # -> video 16.5 (fake mapping)
        sentence.cleared_at = 106.0     # -> video 19.0: past the video's end
        meta = {"video_id": "abc", "url": "u", "title": "t", "channel": "c",
                "caption_lang": "id", "caption_auto": False,
                "duration": 18.0}
        src = FakeSource(None, meta, appear_t=16.5, clear_t=19.0)
        draft = build_draft(sentence.words[0], None, None, out_dir=tmp,
                            translator=lambda t, s="": "tx:" + t,
                            screenshot_note="no shot", source=src)
        assert src.sliced is not None, "source clip was never cut"
        start, end, _, _ = src.sliced
        assert abs(start - 16.5) < 1e-6, src.sliced
        assert abs(end - 18.0) < 1e-6, src.sliced   # clamped, not 20.0
        with open(os.path.join(draft.folder_path, "metadata.json"),
                  encoding="utf-8") as f:
            m = json.load(f)
        assert m["audio_window"]["end"] == 18.0, m["audio_window"]
        print("PASS builder: the clip never runs past the video's end")


def test_builder_track_extends_end_not_start():
    """card_0006: we cleanly saw the line appear at 49.05 and lose it early
    at 49.52 (a churn/quick-click fragment of its tail). The matched track
    (48.42-50.22) must EXTEND the end past our early clear, but must NOT pull
    the start earlier than our clean appearance — the auto track's 'jangan'
    tag at 48.42 is ASR lead and would open the clip on the previous line."""
    with tempfile.TemporaryDirectory() as tmp:
        sentence = Sentence(
            "jangan banyak alasan ya", (0, 0, 300, 50),
            [("banyak", (100, 0, 200, 50))])
        sentence.appeared_at = 100.0    # -> video 49.05 (fake mapping)
        sentence.cleared_at = 106.0     # -> video 49.52
        match = {"start": 48.42, "end": 50.219, "score": 1.0,
                 "text": "jangan banyak alasan ya"}
        src = FakeSource(match, {"video_id": "vid", "caption_lang": "id",
                                 "caption_auto": True},
                         appear_t=49.05, clear_t=49.52)
        draft = build_draft(sentence.words[0], None, None, out_dir=tmp,
                            translator=lambda t, s="": "tx:" + t,
                            screenshot_note="no shot", source=src, near_t=49.5)
        start, end, _, _ = src.sliced
        assert abs(start - 49.05) < 1e-6, src.sliced   # OUR appear, not 48.42
        assert abs(end - 50.219) < 1e-6, src.sliced     # track extends the end
        with open(os.path.join(draft.folder_path, "metadata.json"),
                  encoding="utf-8") as f:
            m = json.load(f)
        assert m["video_source"]["start_from"] == "onscreen", m["video_source"]
        assert m["video_source"]["end_from"] == "track", m["video_source"]
        assert m["video_source"]["track_window"] == [48.42, 50.219]
        print("PASS builder: the track extends the end but never the start")


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


def test_window_for_caps_phantom_tail():
    """card_0075: in the rolling auto format a word's raw end is the NEXT
    word's start, so the last matched word's end absorbed the whole
    inter-sentence silence -- the clip ran past the sentence into the next
    line, and the cap (centred on the click) then trimmed the real START to
    pay for that phantom tail. The text-match window must cap the last
    token's end at WORD_MAX, exactly as position windows already do."""
    from cappa.source.transcript import WORD_MAX
    from cappa.source.vtt import Token
    toks = []
    for k, w in enumerate(["keadilan", "itu", "harus", "ditegakkan"]):
        toks.append(Token(w, 821.0 + k * 0.4, 821.4 + k * 0.4))
    # _fill_ends left the sentence's last word ending where the NEXT
    # sentence's first word starts, 3.6s of silence later.
    toks[-1] = Token(toks[-1].text, toks[-1].start, 826.2)
    toks.append(Token("selanjutnya", 826.2, 826.6))
    tr = Transcript(toks)
    m = tr.window_for("KEADILAN ITU HARUS DITEGAKKAN")
    assert m and m["i"] == 0 and m["j"] == 4, m
    limit = toks[3].start + WORD_MAX
    assert m["end"] <= limit + 1e-6, (
        "matched window keeps the phantom tail: end %.2f > %.2f" % (m["end"],
                                                                    limit))
    assert m["end"] > toks[3].start, m
    print("PASS phantom-tail: window ends %.2f, silence + next line left out"
          % m["end"])


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


def test_window_at_presilence():
    """The rolling parser chains a token's end to the NEXT token's start, so
    the last word before a silence 'lasts' the whole silence (card_0061:
    'lagi' spanned 8.2s). A position inside that phantom span must still
    reach BACK to the utterance's start — the raw end used to fail the
    max_span test for every backward step — and the window must not carry
    the silence as if it were audio."""
    from cappa.source.vtt import Token
    toks = [Token("korban", 225.2, 225.7), Token("pisang", 225.7, 226.2),
            Token("lagi", 226.2, 234.5),   # pre-silence phantom end
            Token("next", 234.5, 235.0)]
    tr = Transcript(toks)
    w = tr.window_at(227.0)     # paused inside the phantom span
    assert w and w["start"] <= 225.2 + 1e-6, w
    assert w["end"] <= 227.3, w
    assert "korban" in w["text"], w
    print("PASS window_at: pre-silence phantom span reaches the "
          "utterance start")


def test_window_at_prefers_played_line():
    """card_0077: the ASR never transcribed the on-screen sentence (a
    neighbor's phantom end swallowed its span), and the click fell into
    that hole — 2.1s after the last played word ENDED, 1.9s before the
    NEXT line started. Nearest-start distance picked the future line,
    speech that hadn't even happened yet at click time. The line that
    already played must win."""
    from cappa.source.vtt import Token
    toks = [Token("di", 829.92, 830.58), Token("luar", 830.58, 830.70),
            Token("Nalar", 830.70, 835.70),   # phantom end spans the hole
            Token("ini", 835.70, 836.70), Token("mumpung", 836.70, 837.00)]
    tr = Transcript(toks)
    w = tr.window_at(833.806)
    assert w and "Nalar" in w["text"], w
    assert "ini" not in w["text"].split(), w
    assert w["start"] <= 829.92 + 1e-6 and w["end"] <= 831.8, w
    print("PASS window_at: a silence click stays with the line that "
          "already played")


def test_builder_rebuilds_from_onscreen_life():
    """card_0077 end-to-end: the track never heard the clicked sentence at
    all. The clip is the row's on-screen life regardless — no clear yet, so
    the end is predicted from the video's spoken pace."""
    with tempfile.TemporaryDirectory() as tmp:
        sentence = Sentence("Istrinya juga dipikir, mas!", (0, 0, 10, 10),
                            [("dipikir", (0, 0, 5, 5))])
        sentence.appeared_at = 100.0    # monotonic; fake maps it to 832.0
        src = FakeSource(None, {"video_id": "vid", "caption_lang": "id",
                                "caption_auto": True}, appear_t=832.0)
        draft = build_draft(sentence.words[0], None, None, out_dir=tmp,
                            translator=lambda t, s="": "tx:" + t,
                            screenshot_note="no shot", source=src,
                            near_t=833.806)
        start, end, _, _ = src.sliced
        # start = appearance (no buffer); end = predicted spoken end
        # (playback floor 833.806 + tail).
        assert abs(start - 832.0) < 1e-6, src.sliced
        assert 832.0 <= end <= 834.206 + 1e-6, src.sliced
        with open(os.path.join(draft.folder_path, "metadata.json"),
                  encoding="utf-8") as f:
            m = json.load(f)
        assert m["audio_window"]["matched_by"] == "block_life", m["audio_window"]
        assert m["video_source"]["start_seconds"] == 832.0
        assert m["video_source"]["end_from"] == "predicted", m["video_source"]
        print("PASS builder: a line the track never heard is clipped from "
              "its on-screen life")


def test_seek_landing_yields_to_logged_sighting():
    """Cards 2-3 of 2026-07-07: a re-watched line's live stamp is the SEEK
    LANDING, not the caption's pop. When this run's transcript logged the
    real sighting, the earliest time wins and the clip opens at the true
    pop; without one, the landing stamp is all anyone knows and the clip
    opens there."""
    def click(sighting):
        tmp = tempfile.mkdtemp()
        sentence = Sentence("LU GAK BAYAR DIA BUAT NEMENIN LU",
                            (0, 0, 10, 10), [("NEMENIN", (0, 0, 5, 5))])
        sentence.appeared_at = 100.0    # maps to 201.313 — the seek landing
        src = FakeSource(None, {"video_id": "vid", "caption_lang": "en",
                                "caption_auto": True},
                         appear_t=201.313, sighting=sighting)
        draft = build_draft(sentence.words[0], None, None, out_dir=tmp,
                            translator=lambda t, s="": "tx:" + t,
                            screenshot_note="no shot", source=src,
                            near_t=200.965)
        with open(os.path.join(draft.folder_path, "metadata.json"),
                  encoding="utf-8") as f:
            return src.sliced, json.load(f)

    # The first watch logged the row popping at 199.9 and leaving at 201.4:
    # that life IS the window, the landing stamp adds nothing new.
    sliced, m = click(sighting={"start": 199.9, "end": 201.4})
    start, end, _, _ = sliced
    assert abs(start - 199.9) < 1e-6, sliced
    assert abs(end - 201.4) < 1e-6, sliced
    assert m["video_source"]["start_seconds"] == 199.9, m["video_source"]

    # No history: the landing is the earliest known time.
    sliced2, m2 = click(sighting=None)
    assert m2["audio_window"]["matched_by"] == "block_life", m2["audio_window"]
    assert sliced2[0] <= 201.313 + 1e-6, sliced2
    print("PASS builder: a logged sighting outranks a seek-landing stamp")


def test_recalled_sighting_anchors_rewatch():
    """card_0009: watch the caption fully, rewind, rewind again, pause,
    click. The live appearance is a seek landing (worthless), but the FIRST
    watch logged where the row really popped — 'you should have saved where
    the caption popped up'. The recalled sighting must anchor the clip's
    start and, with no live clear, bound its end."""
    from cappa.flashcard import timing as timing_mod
    old_min, old_max = timing_mod.MIN_CLIP, timing_mod.MAX_CLIP
    timing_mod.set_clip_bounds(max_clip=5.0)   # room to see the true window
    try:
        with tempfile.TemporaryDirectory() as tmp:
            sentence = Sentence("Muraji BIAR DIA DAPAT KILL!", (0, 0, 10, 10),
                                [("DAPAT", (0, 0, 5, 5))])
            sentence.appeared_at = 100.0    # live stamp: the rewind landing
            src = FakeSource(None, {"video_id": "vid", "caption_lang": "en",
                                    "caption_auto": True},
                             appear_t=244.507,
                             sighting={"start": 243.4, "end": 244.5})
            draft = build_draft(sentence.words[0], None, None, out_dir=tmp,
                                translator=lambda t, s="": "tx:" + t,
                                screenshot_note="no shot", source=src,
                                near_t=244.249)
            start, end, _, _ = src.sliced
            assert abs(start - 243.4) < 1e-6, src.sliced  # not 244.5
            assert abs(end - 244.5) < 1e-6, src.sliced
            with open(os.path.join(draft.folder_path, "metadata.json"),
                      encoding="utf-8") as f:
                m = json.load(f)
            assert m["video_source"]["start_seconds"] == 243.4, (
                m["video_source"])
            assert m["video_source"]["end_seconds"] == 244.5
    finally:
        timing_mod.set_clip_bounds(min_clip=old_min, max_clip=old_max)
    print("PASS builder: a rewatched row anchors at its logged first "
          "sighting")


def test_rebirth_anchors_at_first_sighting():
    """card_0002 (2026-07-09), real numbers: animated art next to the
    glyphs churned the row — the ledger cleared and re-accepted the SAME
    caption mid-life, and the reborn row's stamp mapped to 40.569 for a
    caption whose logged sighting popped at 39.11 and cleared at 40.569.
    Anchoring at the rebirth cut a clip that missed the clicked word
    entirely. A live stamp preceded that closely by the same text's logged
    clear is a rebirth: the sighting's start anchors, its clear ends."""
    with tempfile.TemporaryDirectory() as tmp:
        sentence = Sentence("tadi kucing saya melahirkan-I", (0, 0, 10, 10),
                            [("melahirkan-I", (0, 0, 5, 5))])
        sentence.appeared_at = 100.0    # live stamp: the rebirth
        src = FakeSource(None, {"video_id": "vid", "caption_lang": "id",
                                "caption_auto": True},
                         appear_t=40.569,
                         sighting={"start": 39.11, "end": 40.569})
        draft = build_draft(sentence.words[0], None, None, out_dir=tmp,
                            translator=lambda t, s="": "tx:" + t,
                            screenshot_note="no shot", source=src,
                            near_t=40.569)
        start, end, _, _ = src.sliced
        assert abs(start - 39.11) < 1e-6, src.sliced   # the real pop
        assert abs(end - 40.569) < 1e-6, src.sliced    # the real exit
        with open(os.path.join(draft.folder_path, "metadata.json"),
                  encoding="utf-8") as f:
            m = json.load(f)
        assert m["video_source"]["start_seconds"] == 39.11, (
            m["video_source"])
        assert m["video_source"]["end_seconds"] == 40.569
    print("PASS builder: a reborn row anchors at the same line's logged "
          "sighting")


def test_builder_waits_for_the_clear():
    """A mid-life click on a PLAYING video: the clear stamp lands moments
    after the click, and it — not the click — is the sentence's true end
    (card_0077: 'the line left at 13:55, that's what should have been
    recorded'). The source audio is a file, so the builder can afford to
    wait a beat for the vanish instead of chopping at the click."""
    import threading
    import time as _time
    with tempfile.TemporaryDirectory() as tmp:
        sentence = Sentence("KALO PAGI PILIH KOBO", (0, 0, 10, 10),
                            [("PAGI", (0, 0, 5, 5))])
        sentence.appeared_at = 100.0    # -> video 822.5 (fake mapping)
        src = FakeSource(None, {"video_id": "vid", "caption_lang": "en",
                                "caption_auto": False},
                         appear_t=822.5, clear_t=823.4, paused=False)

        def clear_later():
            _time.sleep(0.3)
            sentence.cleared_at = 106.0   # -> video 823.4 (fake mapping)

        threading.Thread(target=clear_later, daemon=True).start()
        draft = build_draft(sentence.words[0], None, None, out_dir=tmp,
                            translator=lambda t, s="": "tx:" + t,
                            screenshot_note="no shot", source=src,
                            near_t=828.0)
        start, end, _, _ = src.sliced
        # ends at the awaited clear (provenance is exact; the clip may widen
        # a touch past it to satisfy the min-clip floor), never at the click.
        assert 823.4 - 1e-6 <= end <= 823.4 + 0.11, src.sliced
        with open(os.path.join(draft.folder_path, "metadata.json"),
                  encoding="utf-8") as f:
            m = json.load(f)
        assert m["video_source"]["end_seconds"] == 823.4, m["video_source"]
        print("PASS builder: a playing video's clip waits for the clear "
              "and ends there")


def test_live_end_prefers_fresh_position():
    """After a timed-out wait the line is STILL up: the freshest playback
    position (words heard during the wait), not the click, bounds the clip
    — unless the bridge can't say or reports a seek away."""
    from cappa.flashcard.clip import PAUSE_TAIL, _live_end

    class Fresh:
        def play_time(self):
            return 831.9

    class Seeked:
        def play_time(self):
            return 999.0

    class Mute:
        pass

    assert abs(_live_end(Fresh(), 830.0) - (831.9 + PAUSE_TAIL)) < 1e-9
    assert abs(_live_end(Seeked(), 830.0) - (830.0 + PAUSE_TAIL)) < 1e-9
    assert abs(_live_end(Mute(), 830.0) - (830.0 + PAUSE_TAIL)) < 1e-9
    print("PASS live-end: fresh position bounds the clip, seeks distrusted")


def test_builder_life_outranks_auto_text():
    """The 2026-07-07 priority, end to end: an AUTO track's text match —
    even a strong one — must not outrank the row's SEEN on-screen life.
    Here the ASR match sits 4s after the row appeared (a duplicate line
    later in the video); the clip must follow the life, not the match."""
    with tempfile.TemporaryDirectory() as tmp:
        sentence = Sentence("kobo kanaeru main valo", (0, 0, 10, 10),
                            [("kobo", (0, 0, 5, 5))])
        sentence.appeared_at = 100.0    # monotonic; fake maps it to 830.0
        match = {"start": 834.0, "end": 836.5, "score": 0.9,
                 "text": "kobo kanaeru main valo", "by": "text"}
        src = FakeSource(match, {"video_id": "vid", "caption_lang": "id",
                                 "caption_auto": True}, appear_t=830.0)
        draft = build_draft(sentence.words[0], None, None, out_dir=tmp,
                            translator=lambda t, s="": "tx:" + t,
                            screenshot_note="no shot", source=src,
                            near_t=831.0)
        start, end, _, _ = src.sliced
        assert end <= 832.0 + 1e-6, src.sliced   # not the ASR's 834-836.5
        assert start <= 830.0 <= end, src.sliced
        with open(os.path.join(draft.folder_path, "metadata.json"),
                  encoding="utf-8") as f:
            m = json.load(f)
        assert m["audio_window"]["matched_by"] == "block_life", m["audio_window"]
        assert m["video_source"]["start_seconds"] == 830.0
        print("PASS builder: the seen life beats a strong auto-track match")


def test_builder_onscreen_when_track_has_hole():
    """card_0080: the dot was green (transcript ready) but the ASR track had
    a hole wider than window_at's reach at the click — text AND position
    matches both came up empty, and the card fell to a silent loopback and
    lost its audio. With source audio and the bridge mapping available, the
    clip must instead be cut from the source at the row's on-screen life."""
    with tempfile.TemporaryDirectory() as tmp:
        sentence = Sentence("Kobo INI MANCA KALO MAIN VALO", (0, 0, 10, 10),
                            [("MAIN", (0, 0, 5, 5))])
        sentence.appeared_at = 100.0    # monotonic; fake maps it to 850.2
        src = FakeSource(None, {"video_id": "vid", "caption_lang": "id",
                                "caption_auto": True}, pos=None,
                         appear_t=850.2)
        draft = build_draft(sentence.words[0], None, None, out_dir=tmp,
                            translator=lambda t, s="": "tx:" + t,
                            screenshot_note="no shot", source=src,
                            near_t=851.5)
        assert src.sliced is not None, "no source clip was cut"
        start, end, _, _ = src.sliced
        # life = [850.2 (no buffer), predicted spoken end]
        assert abs(start - 850.2) < 1e-6, src.sliced
        assert 850.2 <= end <= 851.9 + 1e-6, src.sliced
        with open(os.path.join(draft.folder_path, "metadata.json"),
                  encoding="utf-8") as f:
            m = json.load(f)
        assert m["audio_window"]["matched_by"] == "block_life", m["audio_window"]
        assert m["video_source"]["start_seconds"] == 850.2
        print("PASS builder: a track hole still cuts source audio from the "
              "row's on-screen life")


def test_builder_clip_always_covers_the_click():
    """A block whose life outruns the clip cap: the cap must centre on the
    CLICK, so the clicked word's moment stays inside whatever gets cut."""
    with tempfile.TemporaryDirectory() as tmp:
        sentence = Sentence("spoken words here", (0, 0, 10, 10),
                            [("spoken", (0, 0, 5, 5)), ("here", (6, 0, 10, 10))])
        sentence.appeared_at = 100.0    # -> video 6.0 (fake mapping)
        sentence.cleared_at = 106.0     # -> video 16.0: a 10 s life
        src = FakeSource(None, {"video_id": "vid", "caption_lang": "en",
                                "caption_auto": True},
                         appear_t=6.0, clear_t=16.0)
        draft = build_draft(sentence.words[0], None, None, out_dir=tmp,
                            translator=lambda t, s="": "tx:" + t,
                            screenshot_note="no shot", source=src,
                            near_t=13.5)
        start, end, _, _ = src.sliced
        assert abs((end - start) - 3.0) < 1e-6, src.sliced   # the cap
        assert start <= 13.5 <= end, (
            "clip %r does not contain the clicked word at 13.5" % (src.sliced,))
        with open(os.path.join(draft.folder_path, "metadata.json"),
                  encoding="utf-8") as f:
            m = json.load(f)
        assert m["audio_window"]["end"] >= 13.5, m["audio_window"]
        print("PASS builder: the clip cap centres on the click "
              "(%.2f-%.2f)" % (start, end))


def test_builder_appearance_anchor():
    """A position-matched clip anchors at the caption's appearance stamp,
    not around the playback position at card time: card_0061 was paused
    past the line and the old near_t-centred cap cut the sentence's start.
    The stamp is already lag-corrected at mapping time (detection
    APPEAR_LAG) — the window opens AT it, no blanket backshift (user call,
    2026-07-09: the same row watched three times stamped within ~170 ms;
    1.8s of worst-case padding put the previous sentence on most cards)."""
    with tempfile.TemporaryDirectory() as tmp:
        sentence = Sentence("korban pisang lagi kah", (0, 0, 10, 10),
                            [("korban", (0, 0, 5, 5))])
        sentence.appeared_at = 100.0    # monotonic; fake maps it to 225.4
        pos = {"start": 223.2, "end": 227.6, "score": 0.0,
               "text": "garbled speech run", "by": "position"}
        src = FakeSource(None, {"video_id": "vid", "caption_lang": "id",
                                "caption_auto": True}, pos=pos,
                         appear_t=225.4)
        draft = build_draft(sentence.words[0], None, None, out_dir=tmp,
                            translator=lambda t, s="": "tx:" + t,
                            screenshot_note="no shot", source=src,
                            near_t=227.0)
        start, end, _, _ = src.sliced
        # start = the appearance (no buffer); the position cue is only
        # consulted to predict a missing end, never to move the start.
        assert abs(start - 225.4) < 1e-6, src.sliced
        assert 225.4 <= end, src.sliced
        with open(os.path.join(draft.folder_path, "metadata.json"),
                  encoding="utf-8") as f:
            m = json.load(f)
        assert m["video_source"]["start_seconds"] == 225.4, (
            m["video_source"])
        assert m["video_source"]["click_position"] == 227.0
        print("PASS builder: the clip opens at the appearance stamp")


def test_builder_seen_clear_ends_clip():
    """A SEEN clear ends the clip exactly there — no trim: the stamp is
    already lag-corrected, and anything past it invites the next line's
    audio (user call)."""
    with tempfile.TemporaryDirectory() as tmp:
        sentence = Sentence("KALO PAGI PILIH KOBO", (0, 0, 10, 10),
                            [("PAGI", (0, 0, 5, 5))])
        sentence.appeared_at = 100.0    # -> video 822.5 (fake mapping)
        sentence.cleared_at = 106.0     # -> video 823.4
        src = FakeSource(None, {"video_id": "vid", "caption_lang": "en",
                                "caption_auto": False},
                         appear_t=822.5, clear_t=823.4)
        draft = build_draft(sentence.words[0], None, None, out_dir=tmp,
                            translator=lambda t, s="": "tx:" + t,
                            screenshot_note="no shot", source=src,
                            near_t=828.0)
        start, end, _, _ = src.sliced
        # exact edges in provenance; the clip may widen symmetrically a touch
        # to meet the min-clip floor (life here is 0.9s).
        assert 823.4 - 0.06 <= end <= 823.4 + 0.06, src.sliced
        assert 822.5 - 0.06 <= start <= 822.5 + 0.06, src.sliced
        with open(os.path.join(draft.folder_path, "metadata.json"),
                  encoding="utf-8") as f:
            m = json.load(f)
        assert m["video_source"]["end_seconds"] == 823.4, m["video_source"]
        print("PASS builder: a seen clear ends the clip (no trim)")


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
        sentence.appeared_at = 100.0    # -> video 41.5 (fake mapping)
        sentence.cleared_at = 106.0     # -> video 42.8
        src = FakeSource(None, {"video_id": "vid"},
                         appear_t=41.5, clear_t=42.8,
                         mono=(700010.0, 700012.0), clip_fails=True)
        rec = FakeRecorder()
        draft = build_draft(sentence.words[1], None, rec, out_dir=tmp,
                            translator=lambda t, s="": "tx:" + t,
                            screenshot_note="no shot", source=src, near_t=42.0)
        assert rec.cut is not None, "loopback rescue never cut"
        assert abs(rec.cut[0] - 700010.0) < 1e-6, rec.cut
        with open(os.path.join(draft.folder_path, "metadata.json"),
                  encoding="utf-8") as f:
            m = json.load(f)
        assert m["audio_window"]["source"] == "loopback_block_timed", (
            m["audio_window"])
        assert m["audio_window"]["matched_by"] == "block_life"
        assert any("source audio unavailable" in n for n in m["notes"]), (
            m["notes"])
        assert any("loopback with on-screen timing" in n for n in m["notes"])
        print("PASS builder: loopback rescue cuts the block-timed clock "
              "window")


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

    # cards 0069/0071's video: a manual ENGLISH track next to Indonesian
    # auto captions, video language Indonesian. Language beats source —
    # the id auto track (text-matchable against the hardsubs) must win
    # over the human track in the wrong language.
    info4 = {"language": "id", "subtitles": {"en": fmts("EN-MAN")},
             "automatic_captions": {"id": fmts("ID-AUTO"),
                                    "ab": fmts("AB")}}
    code4, auto4, url4 = _pick_subtitle(info4, "id", True)
    assert (code4, auto4, url4) == ("id", True, "ID-AUTO"), (code4, url4)
    # A manual track in the REQUESTED language still beats the auto one.
    info5 = {"language": "id", "subtitles": {"id": fmts("ID-MAN"),
                                             "en": fmts("EN-MAN")},
             "automatic_captions": {"id": fmts("ID-AUTO")}}
    code5, auto5, url5 = _pick_subtitle(info5, "id", True)
    assert (code5, auto5, url5) == ("id", False, "ID-MAN"), (code5, url5)
    # Wrong-language manual is still the LAST resort when nothing matches.
    info6 = {"language": None, "subtitles": {"en": fmts("EN-MAN")},
             "automatic_captions": {"ab": fmts("AB")}}
    code6, auto6, url6 = _pick_subtitle(info6, "id", True)
    assert (code6, auto6, url6) == ("en", False, "EN-MAN"), (code6, url6)
    print("PASS pick_subtitle: language beats source; 'ab' never wins")


def test_builder_falls_back_when_no_match():
    with tempfile.TemporaryDirectory() as tmp:
        sentence = Sentence(
            "something not in the track", (0, 0, 10, 10),
            [("something", (0, 0, 5, 5)), ("track", (6, 0, 10, 10))])
        src = FakeSource(None, {})   # no stamps, no sightings: no life
        draft = build_draft(sentence.words[0], None, None, out_dir=tmp,
                            translator=lambda t, s="": "tx:" + t,
                            screenshot_note="no shot", source=src)
        assert src.sliced is None, "should not have cut a clip with no life"
        with open(os.path.join(draft.folder_path, "metadata.json"),
                  encoding="utf-8") as f:
            m = json.load(f)
        assert m["video_source"] is None, "no life must not set video_source"
        assert m["audio_window"] is None, m["audio_window"]
        assert any("recorder" in n for n in m["notes"]), m["notes"]
        print("PASS builder: no mappable life -> loopback fallback path")


def test_builder_snaps_ocr_to_track():
    """The card_0018 failure: OCR read a punctuation glyph as an alif
    (معروف -> معروفا), which broke the word's translation. With a strong text
    match against a HUMAN-MADE track its words are ground truth, so the
    misread word (and the sentence) snap to them BEFORE translation."""
    with tempfile.TemporaryDirectory() as tmp:
        sentence = Sentence(
            "وغير معروفا", (0, 0, 200, 40),
            [("وغير", (0, 0, 90, 40)), ("معروفا", (100, 0, 200, 40))])
        sentence.appeared_at = 100.0    # -> video 10.2 (fake mapping)
        sentence.cleared_at = 106.0     # -> video 11.8
        match = {"start": 10.0, "end": 12.0, "score": 0.82,
                 "text": "وغير معروف", "by": "text"}
        meta = {"video_id": "x", "caption_lang": "ar", "caption_auto": False}
        src = FakeSource(match, meta, appear_t=10.2, clear_t=11.8)
        calls = []

        def spy_translate(t, s=""):
            calls.append((t, s))
            return "tx:" + t

        draft = build_draft(sentence.words[1], None, None, out_dir=tmp,
                            translator=spy_translate,
                            screenshot_note="no shot", source=src,
                            near_t=11.0)
        assert draft.word == "معروف", draft.word
        assert draft.sentence == "وغير معروف", draft.sentence
        # translation ran AFTER the snap, and with the sentence for context
        assert ("معروف", "وغير معروف") in calls, calls
        with open(os.path.join(draft.folder_path, "metadata.json"),
                  encoding="utf-8") as f:
            m = json.load(f)
        assert m["word"] == "معروف", m["word"]
        assert m["word_translation"] == "tx:معروف", m["word_translation"]
        assert m["video_source"]["ocr_sentence"] == "وغير معروفا", (
            m["video_source"])
        assert any("معروفا -> معروف" in n for n in m["notes"]), m["notes"]
        print("PASS snap: phantom alif corrected from the caption track")


def test_snap_guards():
    """No snapping without a STRONG TEXT match, and never across genuinely
    different words -- auto-captions are ASR and may legitimately disagree
    with a burned-in subtitle."""
    from cappa.flashcard.builder import _snap_to_track
    from cappa.flashcard.model import CardDraft

    def draft_with(window, word="hello", sent="hello there"):
        d = CardDraft(word, sent)
        d.word_index = 0
        d.audio_window = window
        return d

    # Position match: the text never aligned, nothing to trust.
    d = draft_with({"matched_by": "position", "score": 0.0,
                    "caption_text": "goodbye there"})
    _snap_to_track(d)
    assert d.sentence == "hello there" and d.word == "hello"

    # AUTO captions: ASR is often wrong; it must NEVER rewrite a burned-in
    # subtitle a person wrote, however strong the alignment.
    d = draft_with({"matched_by": "text", "score": 0.95, "auto": True,
                    "caption_text": "hallo there"})
    _snap_to_track(d)
    assert d.sentence == "hello there" and d.word == "hello"

    # Weak text match: below the strong threshold, leave the OCR alone.
    d = draft_with({"matched_by": "text", "score": 0.70,
                    "caption_text": "hello then"})
    _snap_to_track(d)
    assert d.sentence == "hello there" and d.word == "hello"

    # Strong match but a genuinely different word: not a misread, keep OCR.
    d = draft_with({"matched_by": "text", "score": 0.80,
                    "caption_text": "goodbye there"})
    _snap_to_track(d)
    assert d.sentence == "hello there" and d.word == "hello"

    # Strong match, near-identical word: the misread is fixed, the clicked
    # word rides along, and the note says what changed.
    d = draft_with({"matched_by": "text", "score": 0.80,
                    "caption_text": "hallo there"})
    _snap_to_track(d)
    assert d.sentence == "hallo there" and d.word == "hallo", (
        d.sentence, d.word)
    assert any("hello -> hallo" in n for n in d.notes), d.notes

    # Uneven split (insert/delete): the line broke differently, don't touch.
    d = draft_with({"matched_by": "text", "score": 0.80,
                    "caption_text": "well hello there"})
    _snap_to_track(d)
    assert d.sentence == "hello there" and d.word == "hello"
    print("PASS snap guards: only strong text matches fix near-identical words")


def test_builder_skips_audio_without_video():
    """yt idle at click time means the click wasn't on a video Cappa knows
    about: the loopback buffer holds unrelated system audio then, so nothing
    must be recorded at all — no wav, and the note says why."""

    class SpyRecorder:
        ready = True
        error = ""

        def __init__(self):
            self.calls = []

        def save_wav(self, path, t0, t1):
            self.calls.append((path, t0, t1))
            return 1.0

    with tempfile.TemporaryDirectory() as tmp:
        sentence = Sentence("apa ini", (0, 0, 10, 10),
                            [("apa", (0, 0, 5, 5)), ("ini", (6, 0, 10, 10))])
        src = FakeSource(None, {})
        src.transcript_ready = False
        src.status = "idle"
        rec = SpyRecorder()
        draft = build_draft(sentence.words[0], None, rec, out_dir=tmp,
                            translator=lambda t, s="": "tx:" + t,
                            screenshot_note="no shot", source=src)
        assert rec.calls == [], "loopback must not be cut without a video"
        assert draft.audio_path is None
        with open(os.path.join(draft.folder_path, "metadata.json"),
                  encoding="utf-8") as f:
            m = json.load(f)
        assert m["audio"] is None, m["audio"]
        assert any("no YouTube video detected" in n for n in m["notes"]), (
            m["notes"])
        print("PASS builder: no known video -> no audio recorded at all")


def test_builder_max_clip():
    """A long block life must not produce a long clip: the window is cut
    down to MAX_CLIP, centred on the click position so the clicked word
    stays inside."""
    with tempfile.TemporaryDirectory() as tmp:
        sentence = Sentence("some long line", (0, 0, 10, 10),
                            [("some", (0, 0, 5, 5)), ("line", (6, 0, 10, 10))])
        sentence.appeared_at = 100.0    # -> video 10.0 (fake mapping)
        sentence.cleared_at = 106.0     # -> video 20.0
        src = FakeSource(None, {"video_id": "vid"},
                         appear_t=10.0, clear_t=20.0)
        draft = build_draft(sentence.words[0], None, None, out_dir=tmp,
                            translator=lambda t, s="": "tx:" + t,
                            screenshot_note="no shot", source=src, near_t=17.0)
        assert src.sliced is not None, "source clip was never cut"
        start, end, _, _ = src.sliced
        assert abs((end - start) - 3.0) < 1e-6, src.sliced
        assert abs((start + end) / 2.0 - 17.0) < 1e-6, src.sliced
        assert start >= 9.6 and end <= 19.8 + 1e-6, src.sliced
        assert draft.audio_window["start"] == start, draft.audio_window
        print("PASS builder: long life capped at 3s around the click position")


def test_builder_min_clip():
    """A one-word caption (a blink of on-screen life) must still cut a clip
    of at least one second, centred on the caption."""
    with tempfile.TemporaryDirectory() as tmp:
        sentence = Sentence("ok then", (0, 0, 10, 10),
                            [("ok", (0, 0, 5, 5)), ("then", (6, 0, 10, 10))])
        sentence.appeared_at = 100.0    # -> video 20.0 (fake mapping)
        sentence.cleared_at = 106.0     # -> video 20.3
        src = FakeSource(None, {"video_id": "vid"},
                         appear_t=20.0, clear_t=20.3)
        draft = build_draft(sentence.words[0], None, None, out_dir=tmp,
                            translator=lambda t, s="": "tx:" + t,
                            screenshot_note="no shot", source=src, near_t=20.2)
        assert src.sliced is not None, "source clip was never cut"
        start, end, _, _ = src.sliced
        assert (end - start) >= 1.0 - 1e-6, src.sliced
        # life = [20.0, 20.3] (no buffer); the widen keeps its midpoint 20.15
        assert abs((start + end) / 2.0 - 20.15) < 1e-6, src.sliced
        assert draft.audio_window["start"] == start, draft.audio_window
        assert draft.audio_window["end"] == end, draft.audio_window
        print("PASS builder: sub-second caption widened to a 1s clip")


if __name__ == "__main__":
    test_manual()
    test_auto()
    test_prune_cache()
    test_no_match()
    test_window_at()
    test_window_at_presilence()
    test_window_at_prefers_played_line()
    test_window_for_near()
    test_window_for_caps_phantom_tail()
    test_window_for_rejects_shared_tail()
    test_builder_block_life_times_the_clip()
    test_builder_clip_end_clamped_to_duration()
    test_builder_track_extends_end_not_start()
    test_builder_clip_always_covers_the_click()
    test_builder_waits_for_the_clear()
    test_live_end_prefers_fresh_position()
    test_builder_life_outranks_auto_text()
    test_builder_onscreen_when_track_has_hole()
    test_builder_rebuilds_from_onscreen_life()
    test_seek_landing_yields_to_logged_sighting()
    test_recalled_sighting_anchors_rewatch()
    test_builder_appearance_anchor()
    test_rebirth_anchors_at_first_sighting()
    test_builder_seen_clear_ends_clip()
    test_builder_loopback_rescue()
    test_pick_subtitle_orig()
    test_builder_falls_back_when_no_match()
    test_builder_snaps_ocr_to_track()
    test_snap_guards()
    test_builder_max_clip()
    test_builder_skips_audio_without_video()
    test_builder_min_clip()
    print("ALL PASS")
