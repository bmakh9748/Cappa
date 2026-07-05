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
from .classifier import CaptionClassifier, big_text, text_verdict
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
                 user_area_provider=None, accept_all=False):
        super().__init__()
        # region_provider() -> (left, top, width, height) physical, or None
        # when there's nothing to capture (no target / parked / picking).
        self._region_provider = region_provider
        # user_area_provider() -> True while the tracked region was drawn by
        # the user (Select area / edge-resize) rather than being a whole
        # window. Decides whether the first scan memorises or judges.
        self._user_area_provider = user_area_provider
        # accept_all: every text line the detector finds becomes a live
        # caption — no geometry/text gates, no baseline muting. See
        # classifier.filter for the experiment's rationale.
        self._accept_all = accept_all
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
                    # monotonic clock, shared with the audio recorder's ring
                    # buffer — this is what anchors a caption's audio clip.
                    grab_time = time.monotonic()
                    diff.feed(img)
                    if diff.mask is None:  # first frame / resize: no baseline
                        watcher.reset()
                        classifier.reset()
                        ledger.reset()
                        dirty = True
                        baseline = True
                    else:
                        refreshed = False
                        if self._refresh:
                            # The user's "check again for words": drop every
                            # detection memory (live, seen, pending clears,
                            # burst cooldown) and scan NOW — judging, never
                            # memorising: an explicit refresh must not
                            # baseline-mute what it was asked to find.
                            self._refresh = False
                            refreshed = True
                            watcher.reset()
                            classifier.reset()
                            ledger.reset()
                            dirty = True
                            baseline = False
                            last_scan = 0.0
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
                            if (baseline and not self._user_area()
                                    and not self._accept_all):
                                # Lock-on scan: BIG text is judged (a
                                # stylised caption may already be on screen
                                # and must be caught); smaller text might be
                                # page furniture sitting centre-ish, so it
                                # is memorised unjudged until its content
                                # changes (the fingerprints free the spot).
                                # A USER-DRAWN area skips all of this: the
                                # user said "captions live in here", so the
                                # first scan judges everything. accept_all
                                # skips it too — nothing gets muted, ever.
                                events += self._scan(img, diff, detector,
                                                     reader, classifier,
                                                     watcher, ledger,
                                                     baseline=True,
                                                     grab_time=grab_time)
                            else:
                                events += self._scan(img, diff, detector,
                                                     reader, classifier,
                                                     watcher, ledger,
                                                     self._user_area(),
                                                     accept_all=self._accept_all,
                                                     grab_time=grab_time)
                            baseline = False
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
    def _scan(img, diff, detector, reader, classifier, watcher, ledger,
              user_area=False, baseline=False, accept_all=False,
              grab_time=0.0):
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
        if baseline:
            # Only BIG pre-existing text gets judged at lock-on; the rest
            # is memorised by mark_seen below, unjudged, until it changes.
            fresh = [b for b in fresh if big_text(b, img.shape[:2])]
            print("[cappa] baseline: %d text boxes, %d big enough to judge"
                  % (len(scan), len(fresh)))
        kept = 0
        for box in classifier.filter(fresh, img.shape[:2], user_area,
                                     accept_all):
            # Read the text (~20 ms, only for boxes that got this far). The
            # rules are fail-open: only confirmed junk is rejected, so
            # unreadable scripts keep working exactly as before. accept_all
            # keeps even confirmed junk — hoverable clocks beat missing words.
            sentence, conf = reader.read(img, box)
            why = (None if accept_all else
                   text_verdict(sentence.text if sentence else None, conf))
            if why is not None:
                classifier.last_rejects.append((box, why))
                continue  # mark_seen below remembers it as judged junk
            kept += 1
            ledger.accept(box, diff.sample, DOWNSCALE, sentence, grab_time)
            watcher.watch(box)
            events.append(("appeared", box))
            if sentence and sentence.text:
                print("[cappa]   read %s (%.2f)"
                      % (_printable(sentence.text), conf))
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

    def refresh(self):
        """UI thread: rescan the region from scratch on the next loop pass —
        the launcher's "Refresh words" / Ctrl+Alt+Shift+R. Just flips an
        attribute; the worker loop does the actual resetting on its side."""
        self._refresh = True

    def stop(self):
        self._running = False
