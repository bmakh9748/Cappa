"""The background thread that runs caption detection end to end.

Kept off the Qt UI thread because the neural scan costs ~60-130 ms — enough
to stutter the overlay if it ran inline. Results cross back to the UI through
queued signals, which Qt delivers on the main thread.

The neural scan itself runs on a SECOND helper thread beside the capture
loop (one job in flight, results consumed next loop pass). Two reasons,
both measured: the loop keeps grabbing frames during a scan, so the
vanish-watcher never starves (that starvation is what capped the old
cadence); and the scan clock starts at SUBMISSION, so the scan period is
truly SCAN_INTERVAL instead of interval + scan time. onnxruntime releases
the GIL during inference, so the two threads genuinely overlap. The stages
stay single-owner: detector belongs to the scan thread (after warm),
reader/watcher/ledger to the loop — a scan job carries its frame, diff
sample and grab time with it, and everything downstream of the boxes runs
on the loop against that snapshot, exactly as when it was synchronous. A
reset/refresh bumps a generation counter and a stale in-flight result is
dropped on arrival.

The chain, one stage per file (cheap stages run every frame, the expensive
one only when something changed, and never more often than SCAN_INTERVAL):

    capture.py     grab the tracked region            every frame   ~10 ms
    diff.py        what changed since last frame      every frame   <1 ms
    stability.py   watch live captions for vanishing  every frame   <1 ms
    detector.py    neural text detection (GPU when    on change     ~0.06-0.13 s
                   the venv has DirectML; gpu.py)     (beside the loop)
    ocr.py         read text in accepted boxes        on accept     ~0.01 s
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

import queue
import sys
import threading
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


SCAN_INTERVAL = 0.08       # min seconds between GPU scan SUBMISSIONS (the
                           # cadence is a true interval now that scans run
                           # beside the loop — the old starvation ceiling is
                           # gone: frame grabs continue during a scan, so a
                           # tighter cadence no longer blinds the watcher).
                           # Playing video keeps `dirty` set, so this is the
                           # appear-latency floor during playback. Sized on
                           # the sim: 0.08 measured 155-251 ms appears and
                           # 0.06 bought nothing more (scan cost is the
                           # floor now); one in-flight job at a time means
                           # a big capture self-throttles to its own scan
                           # cost either way. The GPU does the work; the
                           # loop's cost per scan is the pre-shrink + submit.
SCAN_INTERVAL_CPU = 0.2    # the CPU cadence, unchanged from the CPU era:
                           # a CPU scan burns a core whether or not it
                           # blocks the loop, and 0.2 was the measured
                           # sweet spot for that budget (0.5s -> 169-834 ms
                           # appear latency, 0.2s -> 197-538 ms; 0.15 worse).
                           # Also used when placement is UNKNOWN (gpu.py
                           # couldn't read the session) — conservative.
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
        # The warm sequence costs seconds (model loads + DirectML's first-run
        # compile), and run() can't see stop() until it checks: bail between
        # steps so quitting during startup doesn't leave this thread alive
        # past the UI's bounded wait.
        detector.warm()  # pay the model loads now, not on the first caption
        if self._running:
            reader.warm()
        if self._running:
            lexicon.ensure_pack(self._ocr_lang)  # the word-split pack
            jmdict.ensure_pack(self._ocr_lang)   # ja: word-lookup dictionary
        if not self._running:
            capture.close()
            return
        self.detector_ok.emit(detector.ready)
        print("[cappa] detector %s | reader %s"
              % ("ready on %s" % (detector.device or "unknown")
                 if detector.ready else "FAILED TO LOAD",
                 "ready on %s" % (reader.device or "unknown")
                 if reader.ready else "FAILED (no text rules)"))

        # The scan helper thread (see the module docstring): one job in
        # flight at a time, the result consumed by the loop next pass.
        # The detector belongs to THIS thread from here on; the loop keeps
        # the reader/watcher/ledger. A job carries everything its apply
        # step needs, so a scan is judged against the frame it actually
        # scanned, not whatever is on screen when it finishes.
        scan_jobs = queue.Queue(maxsize=1)
        scan_results = queue.Queue()
        scan_done = threading.Event()  # lets _pace wake the moment a scan
                                       # finishes instead of sleeping it off

        def scan_thread():
            while True:
                job = scan_jobs.get()
                if job is None:
                    return
                img, sample, grab_time, gen = job
                try:
                    boxes = detector.scan(img)  # fail-open: [] on failure
                except Exception:               # last net: a lost result
                    boxes = []                  # must never wedge in_flight
                scan_results.put((boxes, img, sample, grab_time, gen))
                scan_done.set()

        threading.Thread(target=scan_thread, name="cappa-scan",
                         daemon=True).start()

        # Cadence keyed to where the sessions actually LANDED (gpu.py's
        # truth channel), not the install probe: unknown placement gets
        # the conservative CPU cadence.
        scan_interval = (SCAN_INTERVAL if detector.device == "gpu"
                         else SCAN_INTERVAL_CPU)
        in_flight = False   # a scan job is out; only one at a time
        generation = 0      # bumped on any reset; stale results are dropped

        dirty = True     # something changed since the last neural scan
        last_scan = 0.0
        frames = 0
        window_start = time.perf_counter()
        try:
            while self._running:
                loop_start = time.perf_counter()

                # A finished scan is applied FIRST, whatever else the pass
                # does: combined with the event-wake in _pace, a result is
                # in the ledger within ~a millisecond of the scan ending.
                # Consuming up here (not inside the tracking branch) also
                # means a parked region can't strand the wake event set —
                # that would turn _pace into a busy loop. If this same pass
                # then resets/parks, the reset wipes what was applied,
                # which is exactly what the reset means.
                scan_events = []
                try:
                    done = scan_results.get_nowait()
                except queue.Empty:
                    pass
                else:
                    scan_done.clear()
                    in_flight = False
                    boxes, s_img, s_sample, s_grab, gen = done
                    if gen == generation:
                        scan_events = self._apply_scan(
                            boxes, s_img, s_sample, reader,
                            watcher, ledger, grab_time=s_grab)
                    # else: a reset/refresh outdated it — dropped.

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
                    generation += 1  # outdate any in-flight scan
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
                        generation += 1
                        dirty = True
                    else:
                        refreshed = False
                        if self._refresh:
                            # The user's "check again for words": drop every
                            # detection memory (live captions, pending
                            # clears) and scan NOW. The emit below clears
                            # the overlay's hotspots this same pass; the
                            # fresh scan repopulates them when it lands.
                            self._refresh = False
                            refreshed = True
                            watcher.reset()
                            ledger.reset()
                            generation += 1
                            dirty = True
                            last_scan = 0.0
                        events = scan_events
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
                                # (At the GPU cadence the natural interval is
                                # already <= RESCAN_AFTER_CLEAR, so this only
                                # moves the clock on the CPU cadence.)
                                last_scan = min(
                                    last_scan,
                                    time.perf_counter() - scan_interval
                                    + RESCAN_AFTER_CLEAR)
                        for box in ledger.expire_clears():
                            events.append(("cleared", box))
                        if (dirty and not in_flight
                                and time.perf_counter() - last_scan
                                >= scan_interval):
                            scan_jobs.put((img, diff.sample, grab_time,
                                           generation))
                            in_flight = True
                            # Submission time, not completion: the cadence
                            # is a true interval (period used to be
                            # interval + scan cost).
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

                self._pace(loop_start, scan_done)
        finally:
            try:
                scan_jobs.put_nowait(None)  # scanner is a daemon; this just
            except queue.Full:              # lets it exit promptly
                pass
            capture.close()

    @staticmethod
    def _apply_scan(scan, img, sample, reader, watcher, ledger,
                    grab_time=0.0):
        """Apply one finished neural scan: bookkeeping against the ledger,
        read what's new, start watching it. `scan`, `img`, `sample` and
        `grab_time` are the scan job's snapshot — the frame the boxes were
        found on — so every judgment below is the same as when the scan ran
        inline; only the wall clock has moved (~one scan) since. Returns
        the events. Prints a one-line diagnostic per interesting scan so a
        terminal run shows what the detector saw."""
        events = []
        # A pending clear whose box is back with its accept-time content:
        # something briefly drew over the caption (control-bar gradient,
        # popup). Resume watching it — no events, no flicker.
        for box in ledger.resurrect(scan, sample, DOWNSCALE):
            watcher.watch(box)
            print("[cappa]   blip: caption re-confirmed, clear suppressed")
        # A live caption whose content stopped matching its accept-time
        # fingerprint was replaced in place (new line, same spot, no clean
        # vanish between). Retire it here so THIS scan re-reads the new
        # text as fresh — no manual refresh needed.
        for box in ledger.drifted(sample, DOWNSCALE):
            watcher.unwatch(box)
            events.append(("cleared", box))
            print("[cappa]   live caption content changed; re-reading")
        fresh = ledger.fresh(scan)
        for box in fresh:
            # Read the text (~10 ms, only for genuinely new boxes). Junk
            # text (a clock, a URL, a handle) is stamped, never rejected:
            # the box stays clickable, but the row must not join a caption
            # block, a card sentence, or the transcript (card_0028:
            # '@korrathetaymi', read at confidence 1.000, joined
            # 'DIED ON THE' as one sentence).
            sentence, conf = reader.read(img, box)
            why = text_verdict(sentence.text if sentence else None, conf)
            if why is not None and sentence is not None:
                sentence.junk = why
            ledger.accept(box, sample, DOWNSCALE, sentence, grab_time)
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

    def _pace(self, loop_start, wake=None):
        """Sleep out the frame budget — but wake at once if the scanner
        finishes mid-sleep (`wake`, a threading.Event the loop clears on
        consume), so a result never sits a sleep's length before it's
        applied. Waking early is harmless: the loop just grabs the next
        frame a shade sooner."""
        remaining = self._interval - (time.perf_counter() - loop_start)
        if remaining <= 0:
            return
        if wake is None:
            time.sleep(remaining)
        else:
            wake.wait(remaining)

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
