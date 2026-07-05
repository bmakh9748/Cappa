"""Bookkeeping between neural scans: which captions are live, and which text
boxes we've already judged.

Every detector scan reports ALL text on screen — the caption AND the
persistent stuff (watermarks, UI labels, channel names). Two memories keep
that sane:

    live   caption boxes currently on screen (accepted by the classifier,
           watched for clearing by stability.py)
    seen   text boxes that were scanned and NOT accepted; while a box keeps
           showing up in scans it stays remembered, so a watermark is judged
           once, not on every scan. Entries expire once the text has been
           gone a while, freeing the spot.

A remembered box only suppresses a new one if it matches by overlap AND by a
coarse content fingerprint of the pixels inside. Overlap alone was a trap:
consecutive caption lines land in the same spot with similar-shaped boxes, so
a caption memorised once (e.g. the line on screen when tracking started)
silently muted every line after it. With the fingerprint, the same spot
showing NEW text is fresh; only truly unchanged furniture stays suppressed.
Cleared captions are not remembered at all.

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
SEEN_TTL = 4.0         # seconds a vanished (rejected) box stays remembered
MISS_LIMIT = 3         # consecutive scans not seeing a live caption = stale
CLEAR_CONFIRM = 0.35   # seconds a clear stays pending before it surfaces —
                       # long enough for the post-clear fast rescan
                       # (RESCAN_AFTER_CLEAR + one scan) to resurrect a blip
FP_BLOCKS = (2, 4)     # fingerprint grid over the box (rows, cols)
FP_TOLERANCE = 14      # max per-block grey delta (0-255) = "same content"


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
        self._seen = []   # [box, last_seen, fingerprint] of rejected text
        self._misses = {}  # live box -> consecutive scans that didn't see it
        self._fps = {}     # live box -> fingerprint captured at accept time
        self._sentences = {}  # live box -> Sentence (text + Words)
        self._pending = []  # [box, fp, deadline, sentence] awaiting confirm

    def live(self):
        return list(self._live)

    def captions(self):
        """What the overlay renders: the live captions as Sentences."""
        return [self._sentences[box] for box in self._live]

    def fresh(self, scan_boxes, sample=None, scale=1):
        """The scan's boxes that are genuinely new: not a live caption, and
        not remembered rejected text STILL SHOWING THE SAME CONTENT. These
        are what the classifier judges."""
        out = []
        for box in scan_boxes:
            if any(_same(box, x) for x in self._live):
                continue
            fp = _fingerprint(box, sample, scale)
            if any(_same(box, e[0]) and _fp_close(fp, e[2])
                   for e in self._seen):
                continue
            out.append(box)
        return out

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

    def mark_seen(self, scan_boxes, sample=None, scale=1):
        """After judging a scan: refresh/remember every non-live box the scan
        reported (accepted ones are live, everything else is 'seen'), and
        forget entries whose text has been gone longer than SEEN_TTL."""
        now = time.monotonic()
        for box in scan_boxes:
            if any(_same(box, x) for x in self._live):
                continue
            fp = _fingerprint(box, sample, scale)
            for entry in self._seen:
                if _same(box, entry[0]):
                    entry[0], entry[1], entry[2] = box, now, fp
                    break
            else:
                self._seen.append([box, now, fp])
        self._seen = [e for e in self._seen if now - e[1] <= SEEN_TTL]

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
        self._seen = []
        self._misses = {}
        self._fps = {}
        self._sentences = {}
        self._pending = []
