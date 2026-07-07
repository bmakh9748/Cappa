"""Unit test: the OCR transcript ledger — Cappa's own record of every
caption row it watched, appended per video as rows leave the screen."""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


def test_silent_vanish_and_junk_not_logged():
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


if __name__ == "__main__":
    test_row_logged_on_clear()
    test_row_still_up_logged_from_stamp()
    test_silent_vanish_and_junk_not_logged()
    test_blip_loop_logs_once_rewatch_logs_again()
    print("ALL PASS")
