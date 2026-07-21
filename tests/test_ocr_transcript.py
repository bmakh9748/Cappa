"""Unit test: the OCR transcript ledger — Cappa's own record of every
caption row it watched, appended per video as rows leave the screen."""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cappa.detection import APPEAR_LAG
from cappa.detection.sentence import Sentence
from cappa.source.ocr_transcript import OcrTranscriptLog


def _row(text, appeared):
    s = Sentence(text, (0, 0, 10, 10), [])
    s.appeared_at = appeared
    return s


def test_row_logged_on_clear():
    """A row observed live, then cleared: one JSONL line under the video it
    appeared on, on-screen life mapped to video time (detection lags
    subtracted: appear stamp -0.30, clear stamp -0.35)."""
    with tempfile.TemporaryDirectory() as tmp:
        log = OcrTranscriptLog(root=tmp)
        s = _row("Padahal ada yang barusan bilang", 100.0)
        video_at = lambda m: m + 1364.0
        log.observe("vid123", [s], video_at)
        assert not os.listdir(tmp), "still on screen: nothing to write yet"
        s.cleared_at = 101.2
        log.observe("vid123", [], video_at)
        with open(os.path.join(tmp, "vid123.jsonl"), encoding="utf-8") as f:
            rec = json.loads(f.read().strip())
        assert rec["text"].startswith("Padahal"), rec
        assert abs(rec["appeared_video"] - (99.70 + 1364.0)) < 1e-6, rec
        assert abs(rec["cleared_video"] - (100.85 + 1364.0)) < 1e-6, rec
        assert rec["appeared_monotonic"] == 100.0, rec
        print("PASS ledger: a cleared row lands in the video's transcript")


def test_row_still_up_logged_from_stamp():
    """The tracker stamps cleared_at while the row is still in the live
    list for a tick (pending clears): the stamp alone is enough."""
    with tempfile.TemporaryDirectory() as tmp:
        log = OcrTranscriptLog(root=tmp)
        s = _row("halo semuanya", 50.0)
        log.observe("vidABC", [s])
        s.cleared_at = 53.0
        log.observe("vidABC", [s])
        path = os.path.join(tmp, "vidABC.jsonl")
        with open(path, encoding="utf-8") as f:
            rec = json.loads(f.read().strip())
        # No mapping supplied: video-time fields stay null, monos recorded.
        assert rec["appeared_video"] is None and rec["cleared_video"] is None
        assert rec["cleared_monotonic"] == 53.0, rec
        # And it is written exactly once: re-observed while still listed,
        # then again after it finally leaves the list.
        log.observe("vidABC", [s])
        log.observe("vidABC", [])
        with open(path, encoding="utf-8") as f:
            assert len(f.read().strip().splitlines()) == 1, "double-logged"
        print("PASS ledger: stamped-but-listed rows log once, monos always")


def test_no_video_id_logs_nothing():
    """observe() only records when given a live video id. A paused/hidden/
    closed tab yields None (decided by source_wiring.live_video_id), and
    then a frozen or off-screen frame can't be logged as caption life."""
    with tempfile.TemporaryDirectory() as tmp:
        log = OcrTranscriptLog(root=tmp)
        s = _row("NOT THE VIDEO", 100.0)
        s.cleared_at = 100.6
        log.observe(None, [s], lambda m: 5.0)      # no live video
        log.observe(None, [], lambda m: 5.0)
        assert not os.listdir(tmp), "no video id -> nothing logged"

        s2 = _row("REALLY PLAYING", 200.0)
        s2.cleared_at = 200.8
        log.observe("vidX", [s2], lambda m: 10.0 + (m - 200.0))
        log.observe("vidX", [], lambda m: 10.0 + (m - 200.0))
        with open(os.path.join(tmp, "vidX.jsonl"), encoding="utf-8") as f:
            assert [json.loads(l)["text"] for l in f] == ["REALLY PLAYING"]
        print("PASS ledger: no live video id -> nothing logged")


def test_seconds_per_word_measures_this_videos_pace():
    """A line still on screen hasn't finished being spoken; how much is left
    depends on this video's pace, not a constant (user call). The pace is the
    median of life/words over the rows watched — no speaker identification,
    one video one pace."""
    with tempfile.TemporaryDirectory() as tmp:
        log = OcrTranscriptLog(root=tmp)
        assert log.seconds_per_word("vidP") is None, "nothing watched yet"
        for k in range(6):                     # 4 words in 1.2s -> 0.30 s/word
            s = _row("satu dua tiga empat %d" % k, 100.0 + k * 10)
            s.cleared_at = s.appeared_at + 1.5      # 5 words, 1.5s
            log.observe("vidP", [s])
            log.observe("vidP", [])
        rate = log.seconds_per_word("vidP")
        assert abs(rate - 0.30) < 1e-9, rate
        # One-word chunks measure the detector's lag, not speech: ignored.
        for k in range(6):
            s = _row("halo", 300.0 + k * 10)
            s.cleared_at = s.appeared_at + 3.0      # would imply 3.0 s/word
            log.observe("vidP", [s])
            log.observe("vidP", [])
        assert abs(log.seconds_per_word("vidP") - 0.30) < 1e-9
        print("PASS ledger: the video's own pace, from multi-word rows only")


def test_silent_vanish_not_logged():
    """A row that vanished without a clear stamp (region reset) writes
    nothing — better no record than a made-up one. Empty-text rows and
    rows with no video id never enter the ledger at all."""
    with tempfile.TemporaryDirectory() as tmp:
        log = OcrTranscriptLog(root=tmp)
        unstamped = _row("baris hilang", 10.0)
        blank = _row("   ", 11.0)
        log.observe("vidXYZ", [unstamped, blank])
        log.observe("vidXYZ", [])          # both gone, no stamps
        no_vid = _row("tanpa video", 12.0)
        log.observe(None, [no_vid])
        no_vid.cleared_at = 13.0
        log.observe(None, [])
        assert not os.listdir(tmp), os.listdir(tmp)
        print("PASS ledger: silent vanishes, blank rows and no-video rows "
              "stay out")


def test_blip_loop_logs_once_rewatch_logs_again():
    """A PAUSED frame with something flickering over the caption clears and
    resurrects the same row in a loop — the same text within seconds must
    not spam the file ('when the video is paused it should not just keep
    adding forever'). A genuine re-read — a rewind, clears well apart — is
    an observation and is welcome."""
    with tempfile.TemporaryDirectory() as tmp:
        log = OcrTranscriptLog(root=tmp)
        for k in range(5):              # blip loop: clears ~1s apart
            s = _row("baris yang sama", 100.0 + k)
            s.cleared_at = 101.0 + k
            log.observe("vidREP", [s])
            log.observe("vidREP", [])
        s2 = _row("baris yang sama", 300.0)   # rewound and re-read later
        s2.cleared_at = 303.0
        log.observe("vidREP", [s2])
        log.observe("vidREP", [])
        with open(os.path.join(tmp, "vidREP.jsonl"), encoding="utf-8") as f:
            lines = f.read().strip().splitlines()
        assert len(lines) == 2, lines
        print("PASS ledger: blip loops log once, a re-watch logs again")


def test_loop_pass_repeats_are_logged():
    """A Short under 10 s loops INSIDE the repeat window, so the same text
    clearing once per pass was silently discarded as a blip loop — every
    pass after the first went unrecorded. A repeat seen in a NEW pass (the
    bridge's pass counter moved) is a genuine observation; a repeat within
    one pass is still the blip it always was."""
    with tempfile.TemporaryDirectory() as tmp:
        log = OcrTranscriptLog(root=tmp)
        for k in range(3):                    # three passes of an 8 s short
            s = _row("subscribe dulu bang", 100.0 + 8 * k)
            s.cleared_at = 103.0 + 8 * k      # clears 8 s apart: < REPEAT_GAP
            log.observe("vidLoop", [s], pass_id=k)
            log.observe("vidLoop", [], pass_id=k)
        blip = _row("subscribe dulu bang", 119.5)   # flicker inside pass 2
        blip.cleared_at = 120.5
        log.observe("vidLoop", [blip], pass_id=2)
        log.observe("vidLoop", [], pass_id=2)
        with open(os.path.join(tmp, "vidLoop.jsonl"), encoding="utf-8") as f:
            lines = f.read().strip().splitlines()
        assert len(lines) == 3, lines
        print("PASS ledger: each loop pass logs its watch; a blip within "
              "one pass still logs once")


def test_window_hint_recalls_first_sighting():
    """card_0009 (watch -> rewind -> pause -> click): the log holds every
    sighting of the row; window_hint hands back the EARLIEST mapped one
    near the click — the closest thing to the row's true pop — matching
    across OCR spacing/punctuation jitter, and never from a stock phrase
    minutes away."""
    with tempfile.TemporaryDirectory() as tmp:
        log = OcrTranscriptLog(root=tmp)
        video_at = lambda m: m + 143.4       # mono 100 -> video 243.4
        s = _row("Muraji BIAR DIA DAPAT KILL!", 100.0)
        s.cleared_at = 101.45                # -> video ~244.5
        log.observe("vidX", [s], video_at)
        log.observe("vidX", [], video_at)
        # A rewind sighting of the same row logs a LATER appearance.
        s2 = _row("Muraji BIAR DIA DAPAT  KILL !", 120.0)   # OCR jitter
        s2.cleared_at = 121.0
        log.observe("vidX", [s2], lambda m: m + 124.0)      # -> ~243.7
        log.observe("vidX", [], lambda m: m + 124.0)
        hint = log.window_hint("vidX", "Muraji BIAR DIA DAPAT KILL!",
                               near_t=244.2)
        assert hint and abs(hint["start"] - (99.7 + 143.4)) < 1e-6, hint
        assert abs(hint["end"] - (101.1 + 143.4)) < 1e-6, hint
        # Too far from the click: a repeated line elsewhere can't lend time.
        assert log.window_hint("vidX", "Muraji BIAR DIA DAPAT KILL!",
                               near_t=500.0) is None
        assert log.window_hint("vidX", "never seen text") is None
        print("PASS ledger: window_hint recalls the earliest sighting, "
              "fuzzy on punctuation, bounded near the click")


def test_window_hint_never_reads_a_past_run():
    """A card's timing may only cite a caption THIS run watched (user call,
    2026-07-08: "when i reopen, taking from the past times its been open is
    bad"). The transcript file is a record, not an oracle: a fresh log over
    the very same file knows nothing until it watches something itself."""
    with tempfile.TemporaryDirectory() as tmp:
        log = OcrTranscriptLog(root=tmp)
        s = _row("SUDAH SELESAI", 100.0)
        s.cleared_at = 101.45
        log.observe("vidR", [s], lambda m: m + 143.4)
        log.observe("vidR", [], lambda m: m + 143.4)
        assert log.window_hint("vidR", "SUDAH SELESAI", near_t=244.2)
        assert os.path.isfile(os.path.join(tmp, "vidR.jsonl")), "still logged"

        reopened = OcrTranscriptLog(root=tmp)      # the app restarts
        assert reopened.window_hint("vidR", "SUDAH SELESAI",
                                    near_t=244.2) is None
        print("PASS ledger: a reopened app recalls nothing from the file")


def test_window_hint_closes_at_the_sighting_s_last_row():
    """card_0025: a hardsub that TYPES ON is re-read as it grows, logging a
    chain of rows ('NUMPANG' -> 'NUMPANG DI HELIKOPTER' -> '... KEBALIK').
    The first row's clear is only the moment it grew; closing there ended
    the clip 1.1 s early. The sighting closes at its LAST row — but a
    genuine re-watch, seconds of wall clock later, must not extend it."""
    with tempfile.TemporaryDirectory() as tmp:
        log = OcrTranscriptLog(root=tmp)
        # (text, appeared_mono, cleared_mono, appeared_video, cleared_video)
        # — the real chain from transcripts/xnvk1jTAcX4.jsonl.
        chain = [("NUMPANG", 431.656, 432.140, 205.518, 205.983),
                 ("NUMPANG DI HELIKOPTER ..", 432.296, 432.843, 206.189, 206.666),
                 ("NUMPANG DI HELIKOPTER KEBALIK .", 432.937, 433.250, 206.810, 207.073),
                 # a separate viewing 465 s later: same line, never the same
                 # sighting, and its clear may not stretch the one above.
                 ("NUMPANG DI HELIKOPTER KEBALIK", 898.781, 900.203, 207.205, 209.900)]
        for text, m0, m1, v0, v1 in chain:
            s = _row(text, m0)
            s.cleared_at = m1
            # The rows of one chain are ~0.4 s apart, so the appear and clear
            # stamps must be told apart exactly, not by a threshold.
            mapping = (lambda m, a=m0 - APPEAR_LAG, v0=v0, v1=v1:
                       v0 if abs(m - a) < 1e-9 else v1)
            log.observe("vidTyped", [s], mapping)
            log.observe("vidTyped", [], mapping)

        hint = log.window_hint("vidTyped", "NUMPANG DI HELIKOPTER KEBALIK",
                               near_t=207.205)
        assert hint and abs(hint["start"] - 205.518) < 1e-6, hint
        assert abs(hint["end"] - 207.073) < 1e-6, hint   # not 205.983, not 209.9
        print("PASS ledger: a typed-on line closes at its last row, and a "
              "re-watch never extends the sighting")


def test_window_hint_matches_block_rows_and_drops_inverted_ends():
    """card_0016: a stacked caption logs as separate ROWS ('AKU CUMA' +
    'BSie DITONTON, LHO!') while the clicked sentence is the joined BLOCK —
    equality matching missed its own sighting. A row's text contained in
    the block (or vice versa) counts, since a block's rows live and die
    together. Junk rows stay out ('C' must not lend timing to anything),
    and a sighting whose life spans a SEEK (clear mapped before its own
    appearance: 249.65 -> 227.9 in the real log) keeps its start but
    surrenders its end."""
    with tempfile.TemporaryDirectory() as tmp:
        log = OcrTranscriptLog(root=tmp)
        rows = [("BSie DITONTON, LHO!", 100.0, 100.6, 249.012, 249.571),
                ("C", 100.0, 100.6, 249.012, 249.571),        # junk row
                ("BSie DITONTON, LHO!", 150.0, 154.4, 249.65, 227.915)]
        for text, m0, m1, v0, v1 in rows:
            s = _row(text, m0)
            s.cleared_at = m1
            # Canned mapping: appear stamp -> v0, clear stamp -> v1.
            log.observe("vidB", [s],
                        lambda m, a=m0, v0=v0, v1=v1: v0 if m < a + 0.1
                        else v1)
            log.observe("vidB", [],
                        lambda m, a=m0, v0=v0, v1=v1: v0 if m < a + 0.1
                        else v1)
        hint = log.window_hint("vidB", "AKU CUMA BSie DITONTON, LHO!",
                               near_t=249.5)
        assert hint and abs(hint["start"] - 249.012) < 1e-6, hint
        assert abs(hint["end"] - 249.571) < 1e-6, hint
        # The junk row alone can never produce a hint for a real sentence.
        assert log.window_hint("vidB", "totally different words here",
                               near_t=249.5) is None
        # Only the seek-spanning sighting available: start survives, the
        # impossible end is dropped.
        log2 = OcrTranscriptLog(root=tmp)
        log2._seen["vidC"] = [{"text": "BSie DITONTON, LHO!",
                               "appeared_video": 249.65,
                               "cleared_video": 227.915,
                               "appeared_monotonic": 150.0,
                               "cleared_monotonic": 154.4}]
        h2 = log2.window_hint("vidC", "AKU CUMA BSie DITONTON, LHO!",
                              near_t=249.5)
        assert h2 and abs(h2["start"] - 249.65) < 1e-6, h2
        assert h2["end"] is None, h2
        print("PASS ledger: block sentences recall their rows' sightings; "
              "junk and seek-spanned ends stay out")


if __name__ == "__main__":
    test_row_logged_on_clear()
    test_row_still_up_logged_from_stamp()
    test_no_video_id_logs_nothing()
    test_seconds_per_word_measures_this_videos_pace()
    test_silent_vanish_not_logged()
    test_blip_loop_logs_once_rewatch_logs_again()
    test_loop_pass_repeats_are_logged()
    test_window_hint_recalls_first_sighting()
    test_window_hint_never_reads_a_past_run()
    test_window_hint_closes_at_the_sighting_s_last_row()
    test_window_hint_matches_block_rows_and_drops_inverted_ends()
    print("ALL PASS")
