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

from cappa.detection.sentence import (Sentence, Word, caption_block,
                                      click_pool)
from cappa.flashcard import build_draft
from cappa.flashcard.timing import (MAX_CLIP, MIN_CLIP, audio_window,
                                    shrink_to_max, widen_to_min)


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


class _Ghost:
    """A sentence with no appear/clear timestamps: the true worst case."""
    appeared_at = 0.0
    cleared_at = 0.0


t0, t1 = audio_window(_Ghost(), 500.0)
assert t1 - t0 == MAX_CLIP == 3.0, (t0, t1)
assert abs((t0 + t1) / 2.0 - 500.0) < 1e-9, (t0, t1)
print("PASS: no-timestamp fallback is a MAX_CLIP clip centred on the click")

# widen_to_min: short windows grow around their midpoint, the floor clamp
# pushes what it eats onto the end, long windows pass through untouched.
t0, t1 = widen_to_min(10.0, 10.2)
assert abs(t0 - 9.6) < 1e-9 and abs(t1 - 10.6) < 1e-9, (t0, t1)
t0, t1 = widen_to_min(0.1, 0.3, floor=0.0)
assert t0 == 0.0 and abs(t1 - 1.0) < 1e-9, (t0, t1)
assert widen_to_min(5.0, 9.0) == (5.0, 9.0)
print("PASS: widen_to_min centres, clamps at the floor, keeps long windows")

# shrink_to_max: long windows are cut down around the given center (the click
# position), clamped inside the original window; short ones pass through.
assert shrink_to_max(5.0, 7.0) == (5.0, 7.0)                 # under the cap
t0, t1 = shrink_to_max(10.0, 20.0)                           # no center: middle
assert abs(t0 - 13.5) < 1e-9 and abs(t1 - 16.5) < 1e-9, (t0, t1)
t0, t1 = shrink_to_max(10.0, 20.0, center=17.0)              # around the click
assert abs(t0 - 15.5) < 1e-9 and abs(t1 - 18.5) < 1e-9, (t0, t1)
t0, t1 = shrink_to_max(10.0, 20.0, center=25.0)              # clamped inside
assert abs(t0 - 17.0) < 1e-9 and abs(t1 - 20.0) < 1e-9, (t0, t1)
t0, t1 = shrink_to_max(10.0, 20.0, center=10.5)              # near the start
assert abs(t0 - 10.0) < 1e-9 and abs(t1 - 13.0) < 1e-9, (t0, t1)
assert MAX_CLIP == 3.0, MAX_CLIP
print("PASS: shrink_to_max caps at 3s around the click, inside the window")


# The still-on-screen OCR window is capped too: a line that has been sitting
# for 10 seconds must not yield a 10-second clip.
class _Lingering:
    appeared_at = 100.0
    cleared_at = 0.0


t0, t1 = audio_window(_Lingering(), 110.0)
assert abs((t1 - t0) - MAX_CLIP) < 1e-9, (t0, t1)
print("PASS: a lingering line's clip is capped at 3s")

# The settings sliders retune the bounds live: the window functions read the
# module globals at call time, not import-time copies.
import cappa.flashcard.timing as timing_mod

timing_mod.set_clip_bounds(0.5, 1.5)
try:
    t0, t1 = shrink_to_max(10.0, 20.0)
    assert abs((t1 - t0) - 1.5) < 1e-9, (t0, t1)
    t0, t1 = widen_to_min(10.0, 10.1)
    assert abs((t1 - t0) - 0.5) < 1e-9, (t0, t1)
    t0, t1 = audio_window(_Lingering(), 110.0)
    assert abs((t1 - t0) - 1.5) < 1e-9, (t0, t1)
    t0, t1 = audio_window(_Ghost(), 500.0)   # fallback respects a tighter cap
    assert abs((t1 - t0) - 1.5) < 1e-9, (t0, t1)
finally:
    timing_mod.set_clip_bounds(MIN_CLIP, MAX_CLIP)
print("PASS: set_clip_bounds retunes min/max/fallback windows live")


class _Blip:
    """A caption that flashed on and off in 0.2s."""
    appeared_at = 100.0
    cleared_at = 100.2


t0, t1 = audio_window(_Blip(), 101.0)
assert abs((t1 - t0) - MIN_CLIP) < 1e-9, (t0, t1)
print("PASS: a blip caption still yields the 1s minimum clip")


# A capture that ran but heard nothing (muted tab, audio routed to a device
# the recorder wasn't bound to — card_0027) is DISCARDED: no silent wav on
# the card, and the note says why. A normal clip is kept untouched.
class SilentRecorder(FakeRecorder):
    last_clip_peak = 1


class LoudRecorder(FakeRecorder):
    last_clip_peak = 12000


with tempfile.TemporaryDirectory() as tmp:
    sentence = Sentence(
        "hello world",
        (10, 20, 120, 50),
        [("hello", (10, 20, 55, 50)), ("world", (60, 20, 120, 50))],
    )
    sentence.appeared_at = 100.0
    sentence.cleared_at = 102.0
    for recorder, expect_kept in ((SilentRecorder(), False),
                                  (LoudRecorder(), True)):
        draft = build_draft(
            sentence.words[0],
            None,
            recorder,
            out_dir=tmp,
            translator=fake_translate,
            screenshotter=fake_screenshot,
            screenshot_png=b"\x89PNG\r\n\x1a\n",
        )
        with open(os.path.join(draft.folder_path, "metadata.json"),
                  encoding="utf-8") as f:
            meta = json.load(f)
        wav = os.path.join(draft.folder_path, "audio.wav")
        dropped = any("audio discarded" in n for n in meta["notes"])
        assert os.path.exists(wav) is expect_kept, (recorder.last_clip_peak,
                                                    meta["notes"])
        assert (meta["audio"] == "audio.wav") is expect_kept, meta["audio"]
        assert dropped is (not expect_kept), meta["notes"]
    print("PASS: a silent loopback clip is discarded with a note, "
          "a normal one is kept")

# A two-line caption (card_0031: "AKU AKAN MENGHANCURKAN" over "FOKUS NYA
# MEREKA!") is two Sentences in the ledger, one per OCR line. The card must
# carry the WHOLE block: joined text top-to-bottom, union box, word index
# into the joined list — while unrelated live text (a chat line elsewhere,
# tiny HUD text below) stays out of it.
line1 = Sentence(
    "AKU AKAN MENGHANCURKAN", (217, 351, 1061, 412),
    [("AKU", (217, 351, 340, 412)), ("AKAN", (350, 351, 520, 412)),
     ("MENGHANCURKAN", (535, 351, 1060, 412))])
line2 = Sentence(
    "FOKUS NYA MEREKA!", (350, 420, 930, 480),
    [("FOKUS", (350, 420, 560, 480)), ("NYA", (570, 420, 680, 480)),
     ("MEREKA!", (690, 420, 930, 480))])
chat = Sentence(
    "jigsaw: Cheer!", (5, 800, 300, 830),
    [("jigsaw:", (5, 800, 150, 830)), ("Cheer!", (160, 800, 300, 830))])
hud = Sentence(
    "100%", (600, 487, 660, 505),   # right under line2 but half the height
    [("100%", (600, 487, 660, 505))])
for s in (line1, line2, chat, hud):
    s.appeared_at = 100.0

block = caption_block(line2.words[1].sentence, [line1, line2, chat, hud])
assert block == [line1, line2], block

# A block never grows past 3 lines (subtitles don't render more): a taller
# same-height stack (a chat column that slipped past the classifier) is cut
# back to the clicked line and its nearest rows.
stack = []
for i in range(5):
    top = 100 + i * 70
    stack.append(Sentence("row %d" % i, (100, top, 500, top + 60),
                          [("row", (100, top, 300, top + 60)),
                           ("%d" % i, (310, top, 500, top + 60))]))
tall = caption_block(stack[1], stack)
assert tall == stack[:3], [s.text for s in tall]   # clicked row 1 + neighbours
print("PASS: caption blocks cap at 3 lines around the clicked one")

# Rows whose boxes BLEED into each other are still one block. Geometry is
# card_0052's: an outline/glow hardsub whose detector boxes overlap 18px
# vertically (82px-tall rows), which the old strict no-overlap rule read as
# "not stacked" — the card kept only the clicked bottom line. A same-row
# re-read (near-identical box) must still refuse to stack.
glow_top = Sentence('“GENE" DI KILLER SHACK', (414, 653, 1305, 735),
                    [('“GENE"', (414, 653, 640, 735))])
glow_bot = Sentence("UDAH MAU KELAR, GUYS!", (400, 717, 1314, 799),
                    [("KELAR,", (818, 717, 1104, 799))])
reread = Sentence("UDAH MAU KELAR, GUYS!", (402, 719, 1310, 797), [])
bled = caption_block(glow_bot, [glow_top, chat])
assert bled == [glow_top, glow_bot], [s.text for s in bled]
assert caption_block(glow_bot, [reread, chat]) == [glow_bot]
print("PASS: bleeding glow-font rows stack, a same-row re-read never does")

with tempfile.TemporaryDirectory() as tmp:
    draft = build_draft(
        line2.words[1],                      # "NYA", clicked in the SECOND line
        None,
        FakeRecorder(),
        out_dir=tmp,
        translator=fake_translate,
        screenshot_png=b"\x89PNG\r\n\x1a\n",
        captions=[chat, line2, hud, line1],  # ledger order is arbitrary
    )
    with open(os.path.join(draft.folder_path, "metadata.json"),
              encoding="utf-8") as f:
        meta = json.load(f)
    assert meta["sentence"] == "AKU AKAN MENGHANCURKAN FOKUS NYA MEREKA!", (
        meta["sentence"])
    assert meta["word"] == "NYA"
    assert meta["word_index"] == 4, meta["word_index"]
    assert meta["sentence_box"] == [217, 351, 1061, 480], meta["sentence_box"]
    assert meta["sentence_verified"] is True, meta["notes"]
    assert meta["sentence_translation"] == (
        "tx:AKU AKAN MENGHANCURKAN FOKUS NYA MEREKA!")
    print("PASS: a two-line caption joins whole onto the card, "
          "unrelated text stays out")

# click_pool reconciles the click-time caption snapshot with the live list
# at card time. Geometry below is card_0045's: a two-line hardsub whose TOP
# line the ledger was still re-reading when the bottom line was clicked, so
# the click snapshot held only the clicked line and the card lost the line
# above — even though the click screenshot plainly shows it, and the two
# rows pass the stacking test (heights 63/65 px, gap 17 px).
top = Sentence('KAMU GAK TAU DIA "VIRAL"', (460, 153, 1419, 216),
               [('KAMU', (460, 153, 640, 216))])
bot = Sentence("KARENA APA!?", (682, 229, 1201, 304),
               [("KARENA", (697, 229, 989, 304))])
for s in (top, bot):
    s.appeared_at = 100.0

# card_0045: the top line finished detection AFTER the click; by card time
# it is live, so it must join the pool — and the block.
pool = click_pool([bot], [top, bot], bot)
assert top in pool and bot in pool, pool
assert caption_block(bot, pool) == [top, bot]
print("PASS: a sibling line detection missed at click time joins the card")

# The caption changed before "Create Anki card" was pressed: the clicked
# line is no longer live, so only the click-time snapshot can be trusted.
later = Sentence("SUDAH MALAM", (600, 229, 1100, 304),
                 [("SUDAH", (600, 229, 800, 304))])
pool = click_pool([top, bot], [later], bot)
assert pool == [top, bot], pool
print("PASS: once the caption moved on, the click snapshot governs")

# A sibling that truly cleared while the popup sat open: its row is empty
# in the live list, so the snapshot copy fills it.
pool = click_pool([top, bot], [bot], bot)
assert top in pool, pool
print("PASS: a sibling cleared while the popup sat open still makes the card")

# A line that churned while the popup sat open (cleared and re-read, box
# wobbling a few px) must not join twice: the live re-read supersedes the
# snapshot's old copy of that row.
top2 = Sentence('KAMU GAK TAU DIA "VIRAL"', (455, 150, 1415, 214),
                [('KAMU', (455, 150, 636, 214))])
top2.appeared_at = 100.4
pool = click_pool([top, bot], [top2, bot], bot)
assert top2 in pool and top not in pool, pool
assert caption_block(bot, pool) == [top2, bot]
print("PASS: a re-read line joins once, not doubled")

print("ALL PASS")
