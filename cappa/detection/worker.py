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
    classifier.py  caption or not-caption (geometry + text rules)

A caption's life: it appears -> the diff sees change -> the next throttled
scan boxes it -> the ledger says it's new -> the classifier accepts its
geometry -> OCR reads it and the text rules find no junk -> "appeared" is
emitted and the watcher starts guarding its pixels -> the line ends -> the
watcher notices within a frame or two -> the clear is held PENDING while a
follow-up scan confirms it (a brief overlay sliding over the caption must
not flicker it) -> "cleared" is emitted, and the next line is usually
already on screen for that same follow-up scan."""

import sys
import time

from PySide6.QtCore import QObject, Signal, Slot

from .capture import ScreenCapture
from .classifier import CaptionClassifier, text_verdict
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


class CaptureWorker(QObject):
    regions = Signal(object)  # (events, live_caption_boxes)
    fps = Signal(float)       # measured capture rate, ~once per second
    detector_ok = Signal(bool)  # emitted once the neural model load resolves

    def __init__(self, region_provider, target_fps=30,
                 user_area_provider=None):
        super().__init__()
        # region_provider() -> (left, top, width, height) physical, or None
        # when there's nothing to capture (no target / parked / picking).
        self._region_provider = region_provider
        # user_area_provider() -> True while the tracked region was drawn by
        # the user (Select area / edge-resize) rather than being a whole
        # window. Decides whether the first scan memorises or judges.
        self._user_area_provider = user_area_provider
        self._interval = 1.0 / target_fps
        # True from birth, cleared by stop(). run() must not set it, so a
        # stop() that lands before the queued run() even starts still wins.
        self._running = True

    @Slot()
    def run(self):
        capture = ScreenCapture()
        diff = FrameDiff()
        detector = TextDetector()
        reader = TextReader()
        classifier = CaptionClassifier()
        watcher = CaptionWatcher(scale=DOWNSCALE)
        ledger = CaptionLedger()
        detector.warm()  # pay the model loads now, not on the first caption
        reader.warm()
        self.detector_ok.emit(detector.ready)
        print("[cappa] detector %s | reader %s"
              % ("ready" if detector.ready else "FAILED TO LOAD",
                 "ready" if reader.ready else "FAILED (no text rules)"))

        dirty = True     # something changed since the last neural scan
        baseline = True  # first scan after lock-on only memorises the page
        last_scan = 0.0
        frames = 0
        window_start = time.perf_counter()
        try:
            while self._running:
                loop_start = time.perf_counter()

                region = self._region()
                if region is None:
                    # Capture paused (no target / parked / picking): drop all
                    # baselines so resuming can't compare across the gap.
                    diff.reset()
                    watcher.reset()
                    classifier.reset()
                    ledger.reset()
                    dirty = True
                    baseline = True
                else:
                    img = capture.grab(region)
                    diff.feed(img)
                    if diff.mask is None:  # first frame / resize: no baseline
                        watcher.reset()
                        classifier.reset()
                        ledger.reset()
                        dirty = True
                        baseline = True
                    else:
                        events = []
                        if (diff.mask.sum()
                                >= ACTIVITY_FRACTION * diff.mask.size):
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
                            if baseline and not self._user_area():
                                # Memorise what the page ALREADY shows so
                                # pre-existing furniture that happens to sit
                                # centre-ish can't pass as a caption. Only
                                # changed/new text after this gets judged
                                # (the ledger's content fingerprints free a
                                # spot the moment its text changes). A USER-
                                # DRAWN area skips this: the user just said
                                # "captions live in here", so text already on
                                # screen — a paused video's caption — must be
                                # judged by the first scan, not muted.
                                scan = detector.scan(img)
                                ledger.mark_seen(scan, diff.sample, DOWNSCALE)
                                print("[cappa] baseline: %d text boxes "
                                      "memorised" % len(scan))
                            else:
                                events += self._scan(img, diff, detector,
                                                     reader, classifier,
                                                     watcher, ledger)
                            baseline = False
                            last_scan = time.perf_counter()
                            dirty = False
                        if events:
                            self.regions.emit((events, ledger.live()))
                    frames += 1

                if loop_start - window_start >= 1.0:
                    self.fps.emit(frames / (loop_start - window_start))
                    frames = 0
                    window_start = loop_start

                self._pace(loop_start)
        finally:
            capture.close()

    @staticmethod
    def _scan(img, diff, detector, reader, classifier, watcher, ledger):
        """One neural pass: box all text, keep what's new, caption-shaped
        AND caption-worded, start watching it. Returns the events. Prints a
        one-line diagnostic per interesting scan so a terminal run shows what
        the detector saw and why boxes were rejected."""
        scan = detector.scan(img)
        events = []
        # A pending clear whose box is back with its accept-time content:
        # something briefly drew over the caption (control-bar gradient,
        # popup). Resume watching it — no events, no flicker.
        for box in ledger.resurrect(scan, diff.sample, DOWNSCALE):
            watcher.watch(box)
            print("[cappa]   blip: caption re-confirmed, clear suppressed")
        fresh = ledger.fresh(scan, diff.sample, DOWNSCALE)
        kept = 0
        for box in classifier.filter(fresh, img.shape[:2]):
            # Read the text (~20 ms, only for boxes that got this far). The
            # rules are fail-open: only confirmed junk is rejected, so
            # unreadable scripts keep working exactly as before.
            text, conf = reader.read(img, box)
            why = text_verdict(text, conf)
            if why is not None:
                classifier.last_rejects.append((box, why))
                continue  # mark_seen below remembers it as judged junk
            kept += 1
            ledger.accept(box, diff.sample, DOWNSCALE)
            watcher.watch(box)
            events.append(("appeared", box))
            if text:
                print("[cappa]   read %s (%.2f)" % (_printable(text), conf))
        ledger.mark_seen(scan, diff.sample, DOWNSCALE)
        for box in ledger.sweep(scan):  # stale: scans stopped seeing it
            watcher.unwatch(box)
            events.append(("cleared", box))
        if fresh or events:
            print("[cappa] scan: %d text | %d new | %d accepted | %d live"
                  % (len(scan), len(fresh), kept, len(ledger.live())))
            for box, why in classifier.last_rejects:
                print("[cappa]   rejected %s: %s" % (box, why))
        return events

    def _region(self):
        try:
            return self._region_provider()
        except Exception:
            return None

    def _user_area(self):
        if self._user_area_provider is None:
            return False
        try:
            return bool(self._user_area_provider())
        except Exception:
            return False

    def _pace(self, loop_start):
        remaining = self._interval - (time.perf_counter() - loop_start)
        if remaining > 0:
            time.sleep(remaining)

    def stop(self):
        self._running = False
