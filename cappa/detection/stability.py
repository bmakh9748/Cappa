"""Per-pixel stability, used to notice the INSTANT a caption disappears.

The neural detector (detector.py) is the authority on where captions appear,
but it only runs a few times a second — waiting for the next scan to notice a
caption vanished would leave stale highlights (and later, stale hotspots) on
screen. This watcher closes that gap for ~nothing: it keeps a per-pixel
"frames since last change" counter over the diff's masks, and for each
accepted caption box remembers which pixels inside were its stable, edgy
strokes (the glyphs — video keeps moving between the letters, so glyph pixels
are the only stable+edgy ones) AND what value each of those pixels had. A
stroke pixel is "lost" only while it currently differs from that remembered
value: video-compression shimmer (strong at fullscreen 1:1 scale) wobbles a
pixel and returns it, so it self-recovers, while a real clear leaves the
pixels permanently different — whether video moves in behind or a new static
background settles there. When a large share of strokes are lost at once,
the caption is gone: feed() reports it cleared, usually within a frame or
two.

Numpy on the diff's downscaled grid, well under a millisecond per frame.
No Qt, no ML: plain, testable unit."""

import numpy as np

STROKE_QUIET = 4      # frames a pixel must have been still to count as stroke
EDGE_THRESHOLD = 40   # grey gradient (0-255) for a pixel to count as an edge
VALUE_TOLERANCE = 40  # grey delta from the remembered stroke value = "lost"
CLEAR_LOST = 0.35     # this share of strokes lost AT ONCE = caption gone
GRACE_FRAMES = 3      # no clear VERDICTS right after watch() (the box comes
                      # from a scan that is a beat older than the live frame)
MIN_STROKES = 12      # fewer stroke pixels than this: watch the whole box


class _Watched:
    __slots__ = ("box", "rows", "cols", "strokes", "values", "age")

    def __init__(self, box, rows, cols, strokes, values):
        self.box, self.rows, self.cols = box, rows, cols
        self.strokes = strokes
        self.values = values   # grey of the box at watch time
        self.age = 0


class CaptionWatcher:
    def __init__(self, scale):
        self._scale = scale   # full-resolution px -> downscaled grid
        self._quiet = None    # per-pixel frames-since-last-change
        self._sample = None   # latest downscaled BGR frame (for edges)
        self._watched = []

    def feed(self, sample, changed):
        """Call every frame with diff.sample / diff.mask. Returns the boxes
        (as given to watch()) whose captions just vanished."""
        if self._quiet is None or self._quiet.shape != changed.shape:
            self.reset()
            self._quiet = np.zeros(changed.shape, np.int32)
        self._sample = sample
        self._quiet = np.where(changed, 0, self._quiet + 1)

        cleared = []
        for w in list(self._watched):
            w.age += 1
            if w.age <= GRACE_FRAMES:
                continue
            grey = sample[w.rows, w.cols].sum(axis=2) // 3
            lost = np.count_nonzero(
                (np.abs(grey - w.values) > VALUE_TOLERANCE) & w.strokes)
            if lost > CLEAR_LOST * max(np.count_nonzero(w.strokes), 1):
                self._watched.remove(w)
                cleared.append(w.box)
        return cleared

    def watch(self, box):
        """Start watching a full-resolution (l, t, r, b) box (the exact same
        object is returned by feed() when it clears)."""
        if self._quiet is None:
            return
        s = self._scale
        h, w = self._quiet.shape
        l, t, r, b = box
        rows = slice(max(t // s, 0), min(max(b // s, t // s + 1), h))
        cols = slice(max(l // s, 0), min(max(r // s, l // s + 1), w))
        if self._sample is None:
            return
        quiet = self._quiet[rows, cols]
        grey = self._sample[rows, cols].sum(axis=2) // 3
        edges = np.zeros(grey.shape, bool)
        edges[:, 1:] = np.abs(grey[:, 1:] - grey[:, :-1]) > EDGE_THRESHOLD
        strokes = (quiet >= STROKE_QUIET) & edges
        if np.count_nonzero(strokes) < MIN_STROKES:
            strokes = np.ones(quiet.shape, bool)  # flat box: watch everything
        self._watched.append(_Watched(box, rows, cols, strokes.copy(),
                                      grey.copy()))

    def unwatch(self, box):
        self._watched = [w for w in self._watched if w.box != box]

    def reset(self):
        """Capture paused or the region was resized: everything is stale."""
        self._quiet = None
        self._sample = None
        self._watched = []
