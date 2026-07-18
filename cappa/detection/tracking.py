"""Bookkeeping between neural scans: which captions are live.

Every detector scan reports ALL text on screen, and every text line becomes
a live caption (the caption-vs-not gates are gone — user call, 2026-07-09);
the ledger's job is knowing what is ALREADY live so a line is read once, not
on every scan, and noticing when a live line leaves or changes. Each live
box carries a coarse content fingerprint of the pixels inside, taken at
accept time: it's what tells a blip from a real clear (resurrect) and spots
a line replaced in place (drifted).

Clears are debounced, not instant: the watcher can misread a brief overlay
(YouTube's control-bar gradient darkening the caption zone on mouse-move) as
the caption vanishing, which flickered a visible cleared+appeared pair.
clear() therefore parks the box as PENDING instead of surfacing it; the next
scan either resurrect()s it silently (same spot, same accept-time
fingerprint = the caption never really left) or expire_clears() confirms it
after CLEAR_CONFIRM seconds. Real clears surface ~0.3 s later than before;
blips surface nothing at all.

No Qt: plain, testable unit."""

import time

import numpy as np

from .sentence import Sentence

MATCH_OVERLAP = 0.55   # intersection / smaller-area to call two boxes "same"
MISS_LIMIT = 3         # consecutive scans not seeing a live caption = stale
CLEAR_CONFIRM = 0.18   # seconds a clear stays pending before it surfaces —
                       # long enough for a confirming scan to resurrect a
                       # blip. Was 0.35, sized for the 0.2 s scan cadence;
                       # scans now come every ~0.05-0.1 s (async pipeline),
                       # so a blip still gets 2+ scans and a real clear
                       # surfaces twice as fast (boxes lingered — user
                       # report, 2026-07-18)
FP_BLOCKS = (2, 4)     # fingerprint grid over the box (rows, cols)
FP_TOLERANCE = 8       # max per-block grey delta (0-255) = "same content".
                       # Was 14: loose enough that a new caption line over a
                       # remembered spot could read as "unchanged" and stay
                       # muted — the words were on screen but unclickable
                       # until a manual refresh. An untouched box drifts by
                       # ~0-4, so 8 still absorbs wobble and compression.
DRIFT_CONFIRM = 0.30   # seconds a live box's content must STAY different
                       # from its accept-time fingerprint before it's retired
                       # as replaced-in-place — a control-bar gradient
                       # sliding over the caption must not retire it (the
                       # same blip the pending-clear machinery absorbs)


def _same(a, b):
    ix = min(a[2], b[2]) - max(a[0], b[0])
    iy = min(a[3], b[3]) - max(a[1], b[1])
    if ix <= 0 or iy <= 0:
        return False
    smaller = min((a[2] - a[0]) * (a[3] - a[1]),
                  (b[2] - b[0]) * (b[3] - b[1]))
    return ix * iy >= MATCH_OVERLAP * max(smaller, 1)


def _fingerprint(box, sample, scale):
    """Coarse content signature: mean grey over an FP_BLOCKS grid of the box
    area, computed on the diff's downscaled frame (`sample`, BGR int16;
    `scale` maps full-res px to that grid). None when unavailable."""
    if sample is None:
        return None
    h, w = sample.shape[:2]
    l, t, r, b = box
    t0, b0 = max(t // scale, 0), min(max(b // scale, t // scale + 1), h)
    l0, r0 = max(l // scale, 0), min(max(r // scale, l // scale + 1), w)
    if b0 <= t0 or r0 <= l0:
        return None
    grey = sample[t0:b0, l0:r0].sum(axis=2) // 3
    return np.array([
        float(block.mean())
        for band in np.array_split(grey, min(FP_BLOCKS[0], grey.shape[0]), 0)
        for block in np.array_split(band, min(FP_BLOCKS[1], band.shape[1]), 1)
    ])


def _fp_close(a, b):
    """True when two fingerprints plausibly show the same content. Missing
    information (either side None) falls back to trusting the overlap."""
    if a is None or b is None:
        return True
    if len(a) != len(b):
        return False
    return np.abs(a - b).max() <= FP_TOLERANCE


class CaptionLedger:
    def __init__(self):
        self._live = []   # accepted caption boxes
        self._misses = {}  # live box -> consecutive scans that didn't see it
        self._fps = {}     # live box -> fingerprint captured at accept time
        self._sentences = {}  # live box -> Sentence (text + Words)
        self._pending = []  # [box, fp, deadline, sentence] awaiting confirm
        self._drift = {}    # live box -> monotonic time its content stopped
                            # matching the accept-time fingerprint

    def live(self):
        return list(self._live)

    def captions(self):
        """What the overlay renders: the live captions as Sentences."""
        return [self._sentences[box] for box in self._live]

    def fresh(self, scan_boxes):
        """The scan's boxes that are genuinely new: not already live. These
        are what gets read and accepted."""
        return [box for box in scan_boxes
                if not any(_same(box, x) for x in self._live)]

    def accept(self, box, sample=None, scale=1, sentence=None,
               appeared_at=0.0):
        """The fingerprint is taken NOW, while the caption is on screen —
        it's what resurrect() compares against after a suspected clear.
        appeared_at is the capture-clock time of the frame this box was
        detected in — the flashcard's audio clip is anchored to it."""
        self._live.append(box)
        self._fps[box] = _fingerprint(box, sample, scale)
        sentence = sentence or Sentence("", box, [])
        sentence.appeared_at = appeared_at or time.monotonic()
        self._sentences[box] = sentence

    def drifted(self, sample=None, scale=1):
        """Live boxes whose pixels stopped matching their accept-time
        fingerprint: the line was REPLACED IN PLACE (next caption, same
        spot) without a clean vanish in between, so the watcher never fires
        and the stale text would sit unclickable until a manual refresh.
        Retire them. Drift must persist DRIFT_CONFIRM before it counts, so
        a gradient or popup sliding over the caption is absorbed the same
        way pending clears absorb blips.

        Returns [(box, sentence)] — the retired box WITH the sentence it
        was showing, because pixels drifting does not prove the TEXT
        changed (video shimmer drifts fingerprints under unchanged text).
        The caller re-reads the box: same text -> re-accept() with this
        same sentence (appeared_at keeps anchoring the audio clip) and the
        fingerprint quietly re-baselines; different text -> surface the
        clear and read it as fresh."""
        now = time.monotonic()
        out = []
        for box in list(self._live):
            fp = _fingerprint(box, sample, scale)
            old = self._fps.get(box)
            if fp is None or old is None or _fp_close(fp, old):
                self._drift.pop(box, None)
                continue
            first = self._drift.setdefault(box, now)
            if now - first < DRIFT_CONFIRM:
                continue
            self._drift.pop(box, None)
            self._live.remove(box)
            self._misses.pop(box, None)
            self._fps.pop(box, None)
            sent = self._sentences.pop(box, None)
            if sent is not None:
                sent.cleared_at = first  # the content changed back THEN
            out.append((box, sent))
        return out

    def sweep(self, scan_boxes):
        """Safety net behind the pixel watcher: a live caption that several
        consecutive scans no longer see is stale (e.g. it vanished in the
        blink between a scan and its watch). Returns the boxes retired."""
        stale = []
        for box in list(self._live):
            if any(_same(box, b) for b in scan_boxes):
                self._misses.pop(box, None)
                continue
            self._misses[box] = self._misses.get(box, 0) + 1
            if self._misses[box] >= MISS_LIMIT:
                self._live.remove(box)
                self._misses.pop(box, None)
                self._fps.pop(box, None)
                self._drift.pop(box, None)
                sent = self._sentences.pop(box, None)
                if sent is not None:
                    sent.cleared_at = time.monotonic()
                stale.append(box)
        return stale

    def clear(self, box):
        """The watcher says this caption vanished. True if it was live — but
        the clear is only PENDING: it surfaces via expire_clears() unless the
        next scan resurrect()s it (a blip, not a real clear)."""
        if box in self._live:
            self._live.remove(box)
            self._misses.pop(box, None)
            self._drift.pop(box, None)
            sent = self._sentences.pop(box, None)
            if sent is not None:
                # Stamp the vanish NOW (when the watcher noticed) rather than
                # at expire — closest to the true on-screen disappearance,
                # before the CLEAR_CONFIRM debounce. A resurrect() clears it
                # again below if this turns out to be a blip.
                sent.cleared_at = time.monotonic()
            self._pending.append(
                [box, self._fps.pop(box, None),
                 time.monotonic() + CLEAR_CONFIRM, sent])
            return True
        return False

    def resurrect(self, scan_boxes, sample=None, scale=1):
        """Scan boxes matching a pending clear WITH its accept-time content:
        the caption never really left (something briefly drew over it). Put
        them back live, silently. Returns the (re-boxed) captions revived.

        The content check re-fingerprints the PENDING box's own coordinates
        on the current frame — comparing through the scan box instead would
        make a few px of box wobble shift the grid onto background rows and
        break the match (content vs content, never window vs window)."""
        revived = []
        for box in scan_boxes:
            for entry in list(self._pending):
                if (_same(box, entry[0])
                        and _fp_close(_fingerprint(entry[0], sample, scale),
                                      entry[1])):
                    self._pending.remove(entry)
                    self._live.append(box)
                    self._fps[box] = _fingerprint(box, sample, scale)
                    # same text, ~same spot: the Sentence rides through, and
                    # the clear that turned out to be a blip is undone so its
                    # appeared_at still anchors the (unbroken) audio clip.
                    sent = entry[3] or Sentence("", box, [])
                    sent.cleared_at = 0.0
                    self._sentences[box] = sent
                    revived.append(box)
                    break
        return revived

    def expire_clears(self):
        """Pending clears whose confirmation window ran out: the caption is
        really gone. Surface these as cleared."""
        now = time.monotonic()
        gone = [e[0] for e in self._pending if now >= e[2]]
        self._pending = [e for e in self._pending if now < e[2]]
        return gone

    def reset(self):
        self._live = []
        self._misses = {}
        self._fps = {}
        self._sentences = {}
        self._pending = []
        self._drift = {}
