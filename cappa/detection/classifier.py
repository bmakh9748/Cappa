"""Step 5 — caption or not-caption.

The region tracker's job is recall: box every stable text-shaped thing. This
stage's job is precision: keep only the boxes that behave like captions.
Geometry and timing (the class below) separate captions from most UI text;
`text_verdict` (the OCR text rules, applied by the worker to what the
geometry accepted) removes confirmed junk that reads like a clock, a URL or
a handle:

    size         a floor only: tiny text (timestamps, channel names) isn't
                 a readable caption. There is NO ceiling — stylised
                 captions can be huge, and big text skips position rules
                 entirely (chat/UI junk is small)
    shape        captions are wide-ish lines (aspect gate; loose for big
                 text and user-drawn areas — short shouts count)
    position     small centred text reads as captions; small off-centre
                 text is hover-titles/sidebars/chat. Applies only to small
                 text in whole-window mode
    burst        a scroll or page redraw floods the caption zone with many
                 boxes that PASS the rules above at once; captions never
                 arrive as a crowd — reject those and cool down briefly.
                 (Applied after individual judging, so a churning chat
                 overlay — rejected per-box as off-centre — can never drag
                 a real caption down with it.)

There is deliberately NO "matches previously accepted captions" rule: it let
chat lines of caption-ish height sneak in once a real caption seeded it, and
since only centred boxes can ever seed it, it could never admit the
off-centre styles it was meant for. Left-aligned subtitle styles are served
by Select area today and by OCR text rules (real trust) later.

Every rule and weight lives in the constants below, per the plan: tune by
hand against real videos, in one place. No Qt, no ML: plain, testable."""

import time

MIN_HEIGHT_PX = 12       # physical px; smaller text isn't a readable caption.
                         # 12 keeps captions inside small popout windows
                         # (video scaled down = caption glyphs ~12-16px).
BIG_TEXT_FRAC = 0.065    # of region height: text at least this tall is a
                         # caption REGARDLESS of position, in every mode —
                         # captions are allowed to be huge (user rule: there
                         # is no such thing as too big). Sized ABOVE page
                         # furniture: video titles / section headers reach
                         # ~5-6% of a browser window's height (the browser
                         # sim guards this), stylised captions 7%+.
BIG_TEXT_MIN_PX = 36     # ...but never call text 'big' below this: small
                         # regions (popouts, cropped strips) scale everything
MIN_ASPECT = 2.5         # width / height: caption lines are wide and short
LOOSE_MIN_ASPECT = 1.3   # big text / user area: a short shout ("APA?") counts
CENTER_TOLERANCE = 0.22  # |box centre - region centre| as fraction of width.
                         # Generous on purpose: tracking the whole browser in
                         # default YouTube layout puts the video pane (and its
                         # centred captions) ~16% left of the WINDOW centre —
                         # a 0.12 tolerance rejected real captions there.
MAX_BATCH = 3            # more simultaneous appearances = scroll, not captions
BURST_COOLDOWN = 0.7     # seconds to distrust everything after a burst

MIN_READ_CONF = 0.75     # below this the reading is a guess, not evidence
MIN_LETTER_RATIO = 0.3   # letters (any script) / characters; lower = UI junk
_URL_HINTS = ("www.", "http", ".com", ".net", ".org", ".tv", ".gg")


def big_text(box, shape):
    """Text tall enough to be trusted as a caption regardless of position
    (chat/UI/page furniture is smaller). shape: (h, w) of the region."""
    return box[3] - box[1] >= max(BIG_TEXT_MIN_PX, BIG_TEXT_FRAC * shape[0])


def text_verdict(text, confidence):
    """The text rules: None = keep the box, else the reason it's junk.

    FAIL-OPEN on purpose: a rejection needs POSITIVE junk evidence read with
    high confidence. Empty, low-confidence or unreadable text passes —
    geometry already vetted the box, and captions in scripts the recogniser
    can't read must never regress (Japanese worked before OCR landed).
    `str.isalpha()` counts kana/kanji/hangul/…, so non-Latin captions pass
    the letter-ratio gate exactly like English ones."""
    if not text or confidence < MIN_READ_CONF:
        return None
    compact = "".join(text.split()).lower()
    if not compact:
        return None
    letters = sum(ch.isalpha() for ch in compact)
    if letters / len(compact) < MIN_LETTER_RATIO:
        return "junk text %s" % ascii(text)   # clock, score, "1080p60"
    if any(h in compact for h in _URL_HINTS) or compact.startswith("@"):
        return "url/handle %s" % ascii(text)  # watermarks, channel plugs
    return None


class CaptionClassifier:
    def __init__(self):
        self._distrust_until = 0.0
        self.last_rejects = []  # (box, reason) from the latest filter() call

    def filter(self, boxes, shape, user_area=False, accept_all=False):
        """boxes: candidate (l, t, r, b) tuples that appeared THIS frame,
        full-resolution region-local px. shape: (h, w) of the region.
        Returns the ones that behave like captions. Rejections (with the
        rule that fired) are left in `last_rejects` for diagnostics.

        user_area=True means the region was DRAWN BY THE USER around the
        caption zone — an explicit "captions live here". Position rules
        don't apply there: no centredness, no height cap, and a gentler
        aspect gate (stylised captions are often big, off-centre, or short
        exclamations — the exact styles the strict rules exist to reject
        in whole-window mode). Size floor and the burst rule stay.

        accept_all=True stands EVERY gate down — size, aspect, position,
        burst, cooldown: whatever the detector boxed comes back accepted.
        The experiment behind it (user call): too many real caption words
        were being rejected, and the hover-only UI makes loose detection
        cheap — junk text becomes hoverable words, nothing more."""
        self.last_rejects = []
        if accept_all:
            return list(boxes)
        now = time.monotonic()
        if now < self._distrust_until:
            self.last_rejects = [(b, "burst cooldown") for b in boxes]
            return []
        kept = []
        for box in boxes:
            why = self._judge(box, shape, user_area)
            if why is None:
                kept.append(box)
            else:
                self.last_rejects.append((box, why))
        # Burst check AFTER individual judging, on the survivors only: a page
        # scroll floods the caption zone with wide centred lines, so many
        # simultaneous PASSES = scroll. Junk that fails on its own (a churning
        # chat overlay lives off-centre) must never drag the caption down
        # with it — that mistake rejected everything on streams with chat.
        if len(kept) > MAX_BATCH:
            self._distrust_until = now + BURST_COOLDOWN
            self.last_rejects += [(b, "burst") for b in kept]
            return []
        return kept

    def reset(self):
        """Capture paused or region switched: coordinates mean nothing now."""
        self._distrust_until = 0.0
        self.last_rejects = []

    # ------------------------------------------------------------- internals
    def _judge(self, box, shape, user_area=False):
        """None if the box behaves like a caption, else the rule it broke."""
        h, w = shape
        l, t, r, b = box
        bw, bh = r - l, b - t
        if bh < MIN_HEIGHT_PX:
            return "too small (%dpx)" % bh
        # Position rules don't apply when the user drew the region (the box
        # IS the position statement) or when the text is BIG — stylised
        # captions can be huge and sit anywhere, while chat/UI junk is
        # small. Both modes behave the same for such text, per user report.
        if user_area or big_text(box, shape):
            if bw < LOOSE_MIN_ASPECT * bh:
                return "not a text line (aspect %.1f)" % (bw / max(bh, 1))
            return None
        if bw < MIN_ASPECT * bh:
            return "not a text line (aspect %.1f)" % (bw / max(bh, 1))
        if abs((l + r) / 2 - w / 2) <= CENTER_TOLERANCE * w:
            return None
        return "off-centre (%.0f%%)" % (abs((l + r) / 2 - w / 2) / w * 100)
