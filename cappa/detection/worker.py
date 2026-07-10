"""The background thread that runs caption detection end to end.

Kept off the Qt UI thread because the neural scan costs ~60-100 ms — enough
to stutter the overlay if it ran inline. Results cross back to the UI through
queued signals, which Qt delivers on the main thread.

The chain, one stage per file (cheap stages run every frame, the expensive
one only when something changed, and never more often than SCAN_INTERVAL):

    capture.py     grab the tracked region            every frame   ~10 ms
    diff.py        what changed since last frame      every frame   <1 ms
    stability.py   watch live captions for vanishing  every frame   <1 ms
    detector.py    neural text detection              on change     ~0.06-0.1 s
    ocr.py         read text in accepted boxes        on accept     ~0.02 s
    tracking.py    match scans to what's already live
    classifier.py  stamp junk TEXT (clock/URL/handle) so it stays off cards

Every text line the detector finds becomes a live, hoverable caption — the
old caption-vs-not gates (geometry, burst, baseline muting) rejected too
many real words and were deleted (user call, 2026-07-09). A caption's life:
it appears -> the diff sees change -> the next throttled scan boxes it ->
the ledger says it's new -> OCR reads it -> "appeared" is emitted and the
watcher starts guarding its pixels -> the line ends -> the watcher notices
within a frame or two -> the clear is held PENDING while a follow-up scan
confirms it (a brief overlay sliding over the caption must not flicker it)
-> "cleared" is emitted, and the next line is usually already on screen for
that same follow-up scan."""

import sys
import time

from PySide6.QtCore import QObject, Signal, Slot

from .. import jmdict, lexicon
from .capture import ScreenCapture
from .classifier import text_verdict
from .detector import TextDetector
from .diff import FrameDiff, DOWNSCALE
from .ocr import TextReader
from .stability import CaptionWatcher
from .tracking import CaptionLedger

def _printable(s):
    """The console's best rendering of `s`: real characters where the stream
    can encode them (UTF-8 terminals show Japanese as Japanese), backslash
    escapes only where it can't — never a UnicodeEncodeError from a print."""
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    return s.encode(enc, "backslashreplace").decode(enc)


SCAN_INTERVAL = 0.2        # min seconds between neural scans. Playing video
                           # keeps `dirty` set, so this is the scan cadence —
                           # and the appear-latency floor — during playback.
                           # Measured on the realistic sim: 0.5s -> 169-834 ms
                           # latency, 0.2s -> 197-538 ms; 0.15s is WORSE (the
                           # ~60-95 ms scans crowd out frame grabs). ~35-45%
                           # of one core while video plays, vs ~15% at 0.5s.
RESCAN_AFTER_CLEAR = 0.15  # a cleared line usually means a new one is up
ACTIVITY_FRACTION = 0.001  # sampled pixels changing = "something happened"
FORCED_RESCAN = 1.0        # max seconds between scans while tracking, even
                           # on a quiet screen: the automatic version of the
                           # launcher's "Refresh words" — catches whatever
                           # the diff/watcher misjudged, and gives pending
                           # content-drift its confirming scan within ~1s.


class CaptureWorker(QObject):
    regions = Signal(object)  # (events, live_caption_boxes)
    fps = Signal(float)       # measured capture rate, ~once per second
    detector_ok = Signal(bool)  # emitted once the neural model load resolves

    def __init__(self, region_provider, target_fps=30, ocr_lang=None):
        super().__init__()
        # The video's language (settings code like "ar"), deciding which rec
        # model reads caption text. None = the default multi-script model.
        # set_ocr_language() changes it live; the loop applies it.
        self._ocr_lang = ocr_lang
        self._ocr_lang_dirty = False
        # region_provider() -> (left, top, width, height) physical, or None
        # when there's nothing to capture (no target / parked / picking).
        self._region_provider = region_provider
        # Set by refresh() from the UI thread (atomic bool), consumed by the
        # loop: drop every detection memory and rescan right now.
        self._refresh = False
        self._interval = 1.0 / target_fps
        # True from birth, cleared by stop(). run() must not set it, so a
        # stop() that lands before the queued run() even starts still wins.
        self._running = True

    @Slot()
    def run(self):
        capture = ScreenCapture()
        diff = FrameDiff()
        detector = TextDetector()
        reader = TextReader(self._ocr_lang)
        watcher = CaptionWatcher(scale=DOWNSCALE)
        ledger = CaptionLedger()
        detector.warm()  # pay the model loads now, not on the first caption
        reader.warm()
        lexicon.ensure_pack(self._ocr_lang)  # download the word-split pack
        jmdict.ensure_pack(self._ocr_lang)   # ja: the word-lookup dictionary
        self.detector_ok.emit(detector.ready)
        print("[cappa] detector %s | reader %s"
              % ("ready" if detector.ready else "FAILED TO LOAD",
                 "ready" if reader.ready else "FAILED (no text rules)"))

        dirty = True     # something changed since the last neural scan
        last_scan = 0.0
        frames = 0
        window_start = time.perf_counter()
        try:
            while self._running:
                loop_start = time.perf_counter()

                if self._ocr_lang_dirty:
                    # New video language from Settings: swap the rec model and
                    # force a full re-scan so on-screen text is re-read with it.
                    self._ocr_lang_dirty = False
                    reader.set_language(self._ocr_lang)
                    reader.warm()
                    lexicon.ensure_pack(self._ocr_lang)
                    jmdict.ensure_pack(self._ocr_lang)
                    self._refresh = True

                region = self._region()
                if region is None:
                    # Capture paused (no target / parked / picking): drop all
                    # baselines so resuming can't compare across the gap.
                    diff.reset()
                    watcher.reset()
                    ledger.reset()
                    dirty = True
                else:
                    img = capture.grab(region)
                    # monotonic clock, shared with the audio recorder's ring
                    # buffer — this is what anchors a caption's audio clip.
                    grab_time = time.monotonic()
                    diff.feed(img)
                    if diff.mask is None:  # first frame / resize: no baseline
                        watcher.reset()
                        ledger.reset()
                        dirty = True
                    else:
                        refreshed = False
                        if self._refresh:
                            # The user's "check again for words": drop every
                            # detection memory (live captions, pending
                            # clears) and scan NOW.
                            self._refresh = False
                            refreshed = True
                            watcher.reset()
                            ledger.reset()
                            dirty = True
                            last_scan = 0.0
                        events = []
                        if (diff.mask.sum()
                                >= ACTIVITY_FRACTION * diff.mask.size):
                            dirty = True
                        if time.perf_counter() - last_scan >= FORCED_RESCAN:
                            dirty = True
                        for box in watcher.feed(diff.sample, diff.mask):
                            if ledger.clear(box):
                                # PENDING, not emitted: the fast rescan below
                                # either resurrects it (blip) or lets it
                                # expire into a real "cleared". The next line
                                # is probably already up for that same scan.
                                last_scan = min(
                                    last_scan,
                                    time.perf_counter() - SCAN_INTERVAL
                                    + RESCAN_AFTER_CLEAR)
                        for box in ledger.expire_clears():
                            events.append(("cleared", box))
                        if (dirty and time.perf_counter() - last_scan
                                >= SCAN_INTERVAL):
                            events += self._scan(img, diff, detector, reader,
                                                 watcher, ledger,
                                                 grab_time=grab_time)
                            last_scan = time.perf_counter()
                            dirty = False
                        if events or refreshed:
                            # A refresh emits even with no events, so the
                            # overlay's stale hotspots are replaced either way.
                            self.regions.emit((events, ledger.captions()))
                    frames += 1

                if loop_start - window_start >= 1.0:
                    self.fps.emit(frames / (loop_start - window_start))
                    frames = 0
                    window_start = loop_start

                self._pace(loop_start)
        finally:
            capture.close()

    @staticmethod
    def _scan(img, diff, detector, reader, watcher, ledger, grab_time=0.0):
        """One neural pass: box all text, read what's new, start watching
        it. Returns the events. Prints a one-line diagnostic per interesting
        scan so a terminal run shows what the detector saw."""
        scan = detector.scan(img)
        events = []
        # A pending clear whose box is back with its accept-time content:
        # something briefly drew over the caption (control-bar gradient,
        # popup). Resume watching it — no events, no flicker.
        for box in ledger.resurrect(scan, diff.sample, DOWNSCALE):
            watcher.watch(box)
            print("[cappa]   blip: caption re-confirmed, clear suppressed")
        # A live caption whose content stopped matching its accept-time
        # fingerprint was replaced in place (new line, same spot, no clean
        # vanish between). Retire it here so THIS scan re-reads the new
        # text as fresh — no manual refresh needed.
        for box in ledger.drifted(diff.sample, DOWNSCALE):
            watcher.unwatch(box)
            events.append(("cleared", box))
            print("[cappa]   live caption content changed; re-reading")
        fresh = ledger.fresh(scan)
        for box in fresh:
            # Read the text (~20 ms, only for genuinely new boxes). Junk
            # text (a clock, a URL, a handle) is stamped, never rejected:
            # the box stays clickable, but the row must not join a caption
            # block, a card sentence, or the transcript (card_0028:
            # '@korrathetaymi', read at confidence 1.000, joined
            # 'DIED ON THE' as one sentence).
            sentence, conf = reader.read(img, box)
            why = text_verdict(sentence.text if sentence else None, conf)
            if why is not None and sentence is not None:
                sentence.junk = why
            ledger.accept(box, diff.sample, DOWNSCALE, sentence, grab_time)
            watcher.watch(box)
            events.append(("appeared", box))
            if sentence and sentence.text:
                print("[cappa]   read %s (%.2f)"
                      % (_printable(sentence.text), conf))
        for box in ledger.sweep(scan):  # stale: scans stopped seeing it
            watcher.unwatch(box)
            events.append(("cleared", box))
        if fresh or events:
            print("[cappa] scan: %d text | %d new | %d live"
                  % (len(scan), len(fresh), len(ledger.live())))
        return events

    def _region(self):
        try:
            return self._region_provider()
        except Exception:
            return None

    def _pace(self, loop_start):
        remaining = self._interval - (time.perf_counter() - loop_start)
        if remaining > 0:
            time.sleep(remaining)

    def refresh(self):
        """UI thread: rescan the region from scratch on the next loop pass —
        the launcher's "Refresh words" / Ctrl+Alt+Shift+R. Just flips an
        attribute; the worker loop does the actual resetting on its side."""
        self._refresh = True

    def set_ocr_language(self, lang):
        """UI thread: the video language changed in Settings. The loop swaps
        the rec model and re-scans on its next pass."""
        if lang != self._ocr_lang:
            self._ocr_lang = lang
            self._ocr_lang_dirty = True

    def stop(self):
        self._running = False
