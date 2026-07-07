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
                 appear_t=None, clear_t=None, paused=None, steady=None):
        self._match = match
        self._meta = meta
        self._pos = pos
        self._mono = mono          # canned (t0, t1) monotonic window, or None
        self._clip_fails = clip_fails
        self._appear_t = appear_t  # canned appearance video-time, or None
        self._clear_t = clear_t    # canned clear video-time, or None
        self._paused = paused      # canned bridge paused state (None=unknown)
        self._steady = steady      # canned steady-playback answer (None=unknown)
        self.sliced = None

    def is_paused(self):
        return self._paused

    def steady_at(self, mono):
        return self._steady

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


def test_builder_prefers_caption_track():
    with tempfile.TemporaryDirectory() as tmp:
        sentence = Sentence(
            "dari pemikiran muncullah kemajuan", (10, 20, 300, 50),
            [("dari", (10, 20, 50, 50)), ("kemajuan", (60, 20, 300, 50))])
        match = {"start": 16.44, "end": 18.86, "score": 0.96, "i": 0, "j": 4,
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
        print("PASS builder: caption-track audio preferred (window 16.44-18.86)")


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
    """card_0077 end-to-end: the position window is the PREVIOUS line
    ('di luar Nalar' — the track never heard the clicked sentence), so the
    row's appearance maps AFTER that window ends. No track window is right
    there; the clip must be rebuilt from the row's on-screen life and the
    provenance must say so."""
    with tempfile.TemporaryDirectory() as tmp:
        sentence = Sentence("Istrinya juga dipikir, mas!", (0, 0, 10, 10),
                            [("dipikir", (0, 0, 5, 5))])
        sentence.appeared_at = 100.0    # monotonic; fake maps it to 832.0
        pos = {"start": 829.92, "end": 831.7, "score": 0.0,
               "text": "di luar Nalar", "by": "position"}
        src = FakeSource(None, {"video_id": "vid", "caption_lang": "id",
                                "caption_auto": True}, pos=pos,
                         appear_t=832.0)
        draft = build_draft(sentence.words[0], None, None, out_dir=tmp,
                            translator=lambda t, s="": "tx:" + t,
                            screenshot_note="no shot", source=src,
                            near_t=833.806)
        start, end, _, _ = src.sliced
        # life = [832.0 - 1.0 backshift, click + 0.4 tail = 834.206]; the
        # cap around its midpoint keeps the sentence, none of either
        # neighboring line.
        assert start >= 831.0 - 1e-6 and end <= 834.206 + 1e-6, src.sliced
        assert start <= 832.0 <= end, src.sliced
        with open(os.path.join(draft.folder_path, "metadata.json"),
                  encoding="utf-8") as f:
            m = json.load(f)
        assert m["audio_window"]["matched_by"] == "onscreen", m["audio_window"]
        assert m["video_source"]["matched_by"] == "onscreen"
        assert m["video_source"]["onscreen_appeared"] == 832.0
        assert m["video_source"]["anchored_at_appearance"] == 832.0
        assert any("on-screen timing" in n for n in m["notes"]), m["notes"]
        print("PASS builder: a line the track never heard is clipped from "
              "its on-screen life")


def test_unsteady_appearance_cannot_anchor():
    """Cards 2-3 of 2026-07-07: one re-watched line, clicked twice, produced
    two DIFFERENT wrong clips — the user had seeked back, the row 'appeared'
    at the seek landing, and that stamp was trusted. On card 2 the landing
    mapped past the position window's end, so the card_0077 rebuild threw
    the good window away and kept only the sentence's tail; on card 3 it
    anchored the cap. With the bridge witnessing the seek (steady_at ->
    False) the stamp must be ignored: the position window survives, both
    clicks agree, and the card says why. steady=True keeps the old rebuild
    (the gate, not the rebuild, is what changed)."""
    def click(steady):
        tmp = tempfile.mkdtemp()
        sentence = Sentence("LU GAK BAYAR DIA BUAT NEMENIN LU",
                            (0, 0, 10, 10), [("NEMENIN", (0, 0, 5, 5))])
        sentence.appeared_at = 100.0    # maps to 201.313 — the seek landing,
                                        # PAST the position window's end
        pos = {"start": 196.76, "end": 201.26, "score": 0.0,
               "text": "I didn't pay him to accompany Valoran,",
               "by": "position"}
        src = FakeSource(None, {"video_id": "vid", "caption_lang": "en",
                                "caption_auto": True}, pos=pos,
                         appear_t=201.313, steady=steady)
        draft = build_draft(sentence.words[0], None, None, out_dir=tmp,
                            translator=lambda t, s="": "tx:" + t,
                            screenshot_note="no shot", source=src,
                            near_t=200.965)
        with open(os.path.join(draft.folder_path, "metadata.json"),
                  encoding="utf-8") as f:
            return src.sliced, json.load(f)

    sliced, m = click(steady=False)
    start, end, _, _ = sliced
    assert m["audio_window"]["matched_by"] == "position", m["audio_window"]
    assert abs(end - 201.26) < 1e-6, sliced      # window end kept
    assert start <= 199.0, sliced                # not the landing's tail
    assert "anchored_at_appearance" not in m["video_source"], m["video_source"]
    assert any("pause/seek" in n for n in m["notes"]), m["notes"]

    sliced2, m2 = click(steady=True)
    assert m2["audio_window"]["matched_by"] == "onscreen", m2["audio_window"]
    assert abs(sliced2[0] - (201.313 - 1.0)) < 1e-6, sliced2
    print("PASS builder: a seek-landing appearance is refused — the "
          "position window survives; a steady one still rebuilds")


def test_anchor_trims_runon_start():
    """card_0005: paused mid-life and clicked; the ASR run-on walked the
    position window 4s into the PREVIOUS sentences, and because the window
    already sat at cap length the appearance only 'centred' it — no trim
    ('started too early by a lot'). A trusted appearance must BOUND the
    start: backshift + grace behind the pop, nothing older."""
    with tempfile.TemporaryDirectory() as tmp:
        sentence = Sentence('NGAPAIN KAU LIHAT "STREAM" AKU?', (0, 0, 10, 10),
                            [("KAU", (0, 0, 5, 5))])
        sentence.appeared_at = 100.0
        pos = {"start": 65.659, "end": 70.159, "score": 0.0,
               "text": "previous sentences plus this one", "by": "position"}
        src = FakeSource(None, {"video_id": "vid", "caption_lang": "en",
                                "caption_auto": True}, pos=pos,
                         appear_t=69.7, steady=True)
        draft = build_draft(sentence.words[0], None, None, out_dir=tmp,
                            translator=lambda t, s="": "tx:" + t,
                            screenshot_note="no shot", source=src,
                            near_t=69.759)
        start, end, _, _ = src.sliced
        from cappa.flashcard.clip import APPEAR_BACKSHIFT, APPEAR_TRIM_GRACE
        lead = APPEAR_BACKSHIFT + APPEAR_TRIM_GRACE
        assert abs(start - (69.7 - lead)) < 1e-6, src.sliced   # not 65.659
        assert end <= 70.159 + 1e-6, src.sliced
        with open(os.path.join(draft.folder_path, "metadata.json"),
                  encoding="utf-8") as f:
            m = json.load(f)
        assert m["video_source"]["anchored_at_appearance"] == 69.7
        print("PASS builder: the seen start bounds a run-on position window")


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
        pos = {"start": 821.0, "end": 860.0, "score": 0.0,
               "text": "giant blob cue", "by": "position"}
        src = FakeSource(None, {"video_id": "vid", "caption_lang": "en",
                                "caption_auto": False}, pos=pos,
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
        assert abs(end - 823.4) < 1e-6, src.sliced   # the awaited clear
        with open(os.path.join(draft.folder_path, "metadata.json"),
                  encoding="utf-8") as f:
            m = json.load(f)
        assert m["video_source"]["onscreen_end"] == 823.4, m["video_source"]
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
        pos = {"start": 829.5, "end": 831.9, "score": 0.0,
               "text": "speech run", "by": "position"}
        src = FakeSource(match, {"video_id": "vid", "caption_lang": "id",
                                 "caption_auto": True}, pos=pos,
                         appear_t=830.0)
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
        assert m["audio_window"]["matched_by"] == "position", m["audio_window"]
        assert m["video_source"]["anchored_at_appearance"] == 830.0
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
        # life = [849.2 (appearance - backshift), 851.9 (click + tail)]
        assert start >= 849.2 - 1e-6 and end <= 851.9 + 1e-6, src.sliced
        assert start <= 850.2 <= end, src.sliced
        with open(os.path.join(draft.folder_path, "metadata.json"),
                  encoding="utf-8") as f:
            m = json.load(f)
        assert m["audio_window"]["matched_by"] == "onscreen", m["audio_window"]
        assert m["video_source"]["matched_by"] == "onscreen"
        assert m["video_source"]["onscreen_appeared"] == 850.2
        assert any("on-screen timing" in n for n in m["notes"]), m["notes"]
        print("PASS builder: a track hole still cuts source audio from the "
              "row's on-screen life")


def test_builder_position_fallback():
    """OCR text that isn't in the caption track (a translated burned-in sub, or
    a garbled auto-caption at the real spot) must fall back to the playback
    position: window_at at click-time near_t."""
    with tempfile.TemporaryDirectory() as tmp:
        sentence = Sentence(
            "english translation not in the track", (0, 0, 10, 10),
            [("english", (0, 0, 5, 5)), ("track", (6, 0, 10, 10))])
        pos = {"start": 40.0, "end": 42.5, "score": 0.0,
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


def test_builder_appearance_anchor():
    """A position-matched clip centres BEHIND the caption's appearance
    stamp, not around the playback position at card time: card_0061 was
    paused past the line and the old near_t-centred cap cut the sentence's
    start; cards 0069/0071 showed the stamp itself trails the speech by a
    variable ~0.7-1.7s, so a window opened forward from it misses the
    word. The window must reach the utterance's start and still contain
    the stamped moment."""
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
        # centre = 225.4 - 1.0 backshift; the run starts at 223.2 and the
        # cap is 2.5s, so the window hugs [223.2, 225.7]: it reaches the
        # utterance's start AND keeps the stamped moment inside.
        assert start <= 225.2 and end >= 225.4, src.sliced
        assert end <= 227.6 + 1e-6, src.sliced
        with open(os.path.join(draft.folder_path, "metadata.json"),
                  encoding="utf-8") as f:
            m = json.load(f)
        assert m["video_source"]["anchored_at_appearance"] == 225.4, (
            m["video_source"])
        assert m["video_source"]["click_position"] == 227.0
        print("PASS builder: position clip centres behind the appearance")


def test_builder_backshift_covers_late_stamp():
    """cards 0069/0071 (VTT ground truth): the clicked row's cue ran
    822.6-823.5 but its appearance stamped 823.3 on one watch and 824.3 on
    the next. A forward window from the stamp held only the NEXT rows'
    audio both times; the backshifted centre must keep the row's cue in
    the clip even on the 824.3 (worst) stamp."""
    with tempfile.TemporaryDirectory() as tmp:
        text = "KALO PAGI PILIH KOBO KALO MALAM PILIH MIKU"
        toks = text.split()
        sentence = Sentence(text, (0, 0, 800, 10),
                            [(w, (i * 100, 0, i * 100 + 90, 10))
                             for i, w in enumerate(toks)])
        sentence.appeared_at = 100.0    # monotonic; fake maps it to 824.28
        pos = {"start": 818.0, "end": 860.0, "score": 0.0,
               "text": "giant blob cue", "by": "position"}
        src = FakeSource(None, {"video_id": "vid", "caption_lang": "en",
                                "caption_auto": False}, pos=pos,
                         appear_t=824.28)
        draft = build_draft(sentence.words[1], None, None, out_dir=tmp,
                            translator=lambda t, s="": "tx:" + t,
                            screenshot_note="no shot", source=src,
                            near_t=825.0)
        start, end, _, _ = src.sliced
        # centre = 824.28 - 1.0 -> window [822.03, 824.53]: PAGI's real cue
        # (822.6-823.5) sits wholly inside. The old forward window
        # [824.28, 826.78] contained none of it.
        assert start <= 822.6 and end >= 823.5, src.sliced
        with open(os.path.join(draft.folder_path, "metadata.json"),
                  encoding="utf-8") as f:
            m = json.load(f)
        assert m["video_source"]["anchored_at_appearance"] == 824.28
        assert m["video_source"]["click_position"] == 825.0
        print("PASS builder: backshifted centre survives a late row stamp")


def test_builder_seen_clear_ends_clip():
    """A SEEN clear ends the clip exactly there — no buffer: the clear
    stamp already trails the real vanish, so padding it just invites the
    next line's audio (user call). Only the pause path, where the end is
    unknown, keeps a small tail (the backshift test covers it)."""
    with tempfile.TemporaryDirectory() as tmp:
        sentence = Sentence("KALO PAGI PILIH KOBO", (0, 0, 10, 10),
                            [("PAGI", (0, 0, 5, 5))])
        sentence.appeared_at = 100.0    # -> video 822.5 (fake mapping)
        sentence.cleared_at = 106.0     # -> video 823.4
        pos = {"start": 821.0, "end": 860.0, "score": 0.0,
               "text": "giant blob cue", "by": "position"}
        src = FakeSource(None, {"video_id": "vid", "caption_lang": "en",
                                "caption_auto": False}, pos=pos,
                         appear_t=822.5, clear_t=823.4)
        draft = build_draft(sentence.words[0], None, None, out_dir=tmp,
                            translator=lambda t, s="": "tx:" + t,
                            screenshot_note="no shot", source=src,
                            near_t=828.0)
        start, end, _, _ = src.sliced
        assert abs(end - 823.4) < 1e-6, src.sliced   # exactly the clear
        assert start <= 822.5, src.sliced
        with open(os.path.join(draft.folder_path, "metadata.json"),
                  encoding="utf-8") as f:
            m = json.load(f)
        assert m["video_source"]["onscreen_end"] == 823.4, m["video_source"]
        print("PASS builder: a seen clear ends the clip with no buffer")


def test_builder_distrusts_impossible_stamp():
    """An appearance mapped LATER than where the user paused is impossible
    for a row they were reading (a re-read's stamp or a paused-seek mapping
    artifact): the click position anchors the cap instead."""
    with tempfile.TemporaryDirectory() as tmp:
        sentence = Sentence("KALO PAGI PILIH KOBO", (0, 0, 10, 10),
                            [("PAGI", (0, 0, 5, 5))])
        sentence.appeared_at = 100.0    # monotonic; fake maps it to 830.0
        pos = {"start": 818.0, "end": 860.0, "score": 0.0,
               "text": "giant blob cue", "by": "position"}
        src = FakeSource(None, {"video_id": "vid", "caption_lang": "en",
                                "caption_auto": False}, pos=pos,
                         appear_t=830.0)
        draft = build_draft(sentence.words[0], None, None, out_dir=tmp,
                            translator=lambda t, s="": "tx:" + t,
                            screenshot_note="no shot", source=src,
                            near_t=824.0)
        start, end, _, _ = src.sliced
        assert abs((start + end) / 2.0 - 824.0) < 1e-6, src.sliced
        with open(os.path.join(draft.folder_path, "metadata.json"),
                  encoding="utf-8") as f:
            m = json.load(f)
        assert "anchored_at_appearance" not in m["video_source"], (
            m["video_source"])
        assert m["video_source"]["click_position"] == 824.0
        print("PASS builder: a stamp after the pause is distrusted")


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


def test_choose_window():
    """The decision core: a strong, nearby text match is trusted for its
    precision; a WEAK text match still wins when it overlaps the position
    window (they agree on the moment, and the text match spans the whole
    on-screen sentence where position is just the speech chunk around the
    click — card_0044); a text match elsewhere loses to position."""
    from cappa.flashcard.clip import choose_window
    strong_near = {"start": 30.0, "end": 33.0, "score": 0.90, "by": "text"}
    strong_far = {"start": 60.0, "end": 63.0, "score": 0.90, "by": "text"}
    weak_near = {"start": 30.0, "end": 33.0, "score": 0.66, "by": "text"}
    weak_off = {"start": 38.0, "end": 41.0, "score": 0.66, "by": "text"}
    pos = {"start": 29.5, "end": 33.5, "score": 0.0, "by": "position"}
    assert choose_window(strong_far, None, None) is strong_far   # no position
    assert choose_window(strong_near, pos, 31.0) is strong_near  # strong+near
    assert choose_window(strong_far, pos, 31.0) is pos           # strong but far
    assert choose_window(weak_near, pos, 31.0) is weak_near      # weak, agrees
    assert choose_window(weak_off, pos, 31.0) is pos             # weak, apart
    assert choose_window(weak_near, None, 31.0) is weak_near     # weak, no pos
    # The 2026-07-07 priority: a SEEN on-screen life outranks any AUTO-track
    # match — even a strong one — while a human-made track keeps its rank.
    assert choose_window(strong_near, pos, 31.0,
                         auto=True, has_life=True) is pos
    assert choose_window(strong_near, None, 31.0,
                         auto=True, has_life=True) is None   # -> on-screen
    assert choose_window(strong_near, pos, 31.0,
                         auto=True, has_life=False) is strong_near
    assert choose_window(strong_near, pos, 31.0,
                         auto=False, has_life=True) is strong_near
    print("PASS choose_window: strong+near wins; weak wins only when it "
          "overlaps the position window; a seen life outranks auto matches")


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


def test_builder_snaps_ocr_to_track():
    """The card_0018 failure: OCR read a punctuation glyph as an alif
    (معروف -> معروفا), which broke the word's translation. With a strong text
    match against a HUMAN-MADE track its words are ground truth, so the
    misread word (and the sentence) snap to them BEFORE translation."""
    with tempfile.TemporaryDirectory() as tmp:
        sentence = Sentence(
            "وغير معروفا", (0, 0, 200, 40),
            [("وغير", (0, 0, 90, 40)), ("معروفا", (100, 0, 200, 40))])
        match = {"start": 10.0, "end": 12.0, "score": 0.82,
                 "text": "وغير معروف", "by": "text"}
        meta = {"video_id": "x", "caption_lang": "ar", "caption_auto": False}
        src = FakeSource(match, meta)
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
        assert any("caption track unavailable" in n for n in m["notes"])
        print("PASS builder: no known video -> no audio recorded at all")


def test_builder_max_clip():
    """A long caption cue must not produce a long clip: the window is cut
    down so the finished clip (window + pre/postroll) is MAX_CLIP, centred
    on the click position so the clicked word stays inside."""
    with tempfile.TemporaryDirectory() as tmp:
        sentence = Sentence("some long line", (0, 0, 10, 10),
                            [("some", (0, 0, 5, 5)), ("line", (6, 0, 10, 10))])
        match = {"start": 10.0, "end": 20.0, "score": 0.9,
                 "text": "a ten second monologue cue", "by": "text"}
        src = FakeSource(match, {"video_id": "vid"})
        draft = build_draft(sentence.words[0], None, None, out_dir=tmp,
                            translator=lambda t, s="": "tx:" + t,
                            screenshot_note="no shot", source=src, near_t=17.0)
        assert src.sliced is not None, "source clip was never cut"
        start, end, pre, post = src.sliced
        assert abs((end - start) + pre + post - 3.0) < 1e-6, src.sliced
        assert abs((start + end) / 2.0 - 17.0) < 1e-6, src.sliced
        assert start >= 10.0 and end <= 20.0, src.sliced
        assert draft.audio_window["start"] == start, draft.audio_window
        print("PASS builder: long cue capped at 3s around the click position")


def test_builder_min_clip():
    """A one-word caption (a fraction of a second in the track) must still cut
    a clip of at least one second, centred on the caption."""
    with tempfile.TemporaryDirectory() as tmp:
        sentence = Sentence("ok then", (0, 0, 10, 10),
                            [("ok", (0, 0, 5, 5)), ("then", (6, 0, 10, 10))])
        match = {"start": 20.0, "end": 20.3, "score": 0.9,
                 "text": "ok then", "by": "text"}
        src = FakeSource(match, {"video_id": "vid"})
        draft = build_draft(sentence.words[0], None, None, out_dir=tmp,
                            translator=lambda t, s="": "tx:" + t,
                            screenshot_note="no shot", source=src, near_t=20.2)
        assert src.sliced is not None, "source clip was never cut"
        start, end, pre, post = src.sliced
        assert (end - start) + pre + post >= 1.0 - 1e-6, src.sliced
        assert abs((start + end) / 2.0 - 20.15) < 1e-6, src.sliced
        assert draft.audio_window["start"] == start, draft.audio_window
        assert draft.audio_window["end"] == end, draft.audio_window
        print("PASS builder: sub-second caption widened to a 1s clip")


if __name__ == "__main__":
    test_manual()
    test_auto()
    test_no_match()
    test_window_at()
    test_window_at_presilence()
    test_window_at_prefers_played_line()
    test_window_for_near()
    test_window_for_caps_phantom_tail()
    test_window_for_rejects_shared_tail()
    test_builder_prefers_caption_track()
    test_builder_position_fallback()
    test_builder_waits_for_the_clear()
    test_live_end_prefers_fresh_position()
    test_builder_life_outranks_auto_text()
    test_builder_onscreen_when_track_has_hole()
    test_builder_rebuilds_from_onscreen_life()
    test_unsteady_appearance_cannot_anchor()
    test_anchor_trims_runon_start()
    test_builder_appearance_anchor()
    test_builder_backshift_covers_late_stamp()
    test_builder_seen_clear_ends_clip()
    test_builder_distrusts_impossible_stamp()
    test_builder_loopback_rescue()
    test_pick_subtitle_orig()
    test_choose_window()
    test_builder_falls_back_when_no_match()
    test_builder_snaps_ocr_to_track()
    test_snap_guards()
    test_builder_max_clip()
    test_builder_skips_audio_without_video()
    test_builder_min_clip()
    print("ALL PASS")
