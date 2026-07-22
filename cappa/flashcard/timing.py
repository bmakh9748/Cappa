"""Audio timing for flashcard drafts."""

# Detection latency between a real on-screen event and our timestamp for it.
# The lags belong to detection (they measure ITS reaction time) and live
# there; imported here so every window function can keep reading timing.*.
from ..detection import APPEAR_LAG, CLEAR_LAG

# Safety padding so a word at the very edge of the line still has its audio.
PREROLL = 0.40
POSTROLL = 0.40
MAX_CLIP = 3.0       # hard cap on any card clip: a card studies ONE word, so
                     # nothing past ~3s of context helps, however long the
                     # line sat on screen or the caption cue ran. Also the
                     # length of the last-resort click-centred window.
MIN_CLIP = 1.0       # no card audio shorter than this, however brief the line

# The Auto length setting: instead of the user's fixed cap, let the clip
# fit whatever window the sentence actually needs (its caption cue or its
# on-screen life), bounded by this ceiling. Measured caption sentences run
# 2.1s median / 7.0s p90 / 9.3s p95: a 5s cap truncated one sentence in
# five.
AUTO_MAX_CLIP = 8.0
AUTO_CLIP = False    # module state like MIN/MAX_CLIP; set from settings

# A caption still on screen has not finished being SPOKEN, and how much is
# left depends on how many of its words are still to come -- a 15-word line
# that popped 100ms ago needs far more tail than a 4-word one.
# The rate is measured from the video's own captions
# (source.seconds_per_word()); these bound a nonsense estimate, and the
# default stands in until enough rows have been watched.
SECONDS_PER_WORD = 0.30      # ~3.3 words/s: unhurried speech
MIN_SECONDS_PER_WORD = 0.12  # nobody speaks faster than ~8 words/s
MAX_SECONDS_PER_WORD = 0.60  # ...nor slower than ~1.7 words/s, sustained
MIN_LIVE_TAIL = 0.15         # a line whose last word is already out still
                             # needs its tail to ring out


def spoken_duration(words, seconds_per_word=None):
    """How long a caption of `words` words takes to say, at this video's
    measured pace. The clip end for a line still on screen is its APPEARANCE
    plus this -- which is equivalent to 'the words not yet spoken, times the
    pace', without pretending the clicked word is the one being spoken (it
    rarely is: the user reads the line, then clicks)."""
    if not words:
        return 0.0
    rate = seconds_per_word or SECONDS_PER_WORD
    rate = min(max(rate, MIN_SECONDS_PER_WORD), MAX_SECONDS_PER_WORD)
    return words * rate


# A live caption whose vanish the pixel watcher missed (a static background
# behind a burned-in line) can sit on the overlay long past its speech, with
# stale hotspots while the next line is already playing. These bound the
# force-clear backstop: the caption track's own cue end is the precise signal,
# a word-count prediction the fallback for videos with no track. Reluctant on
# purpose -- the pixel watcher is the fast, primary path, and a wrong clear
# re-detects within a second.
MIN_OVERSTAY = 2.5           # never expire a caption younger than this: normal
                             # lines clear well before, and clearing a fresh
                             # one would steal a word being reached for
TRACK_OVERSTAY_MARGIN = 1.0  # seconds the playhead must be past the track's
                             # own cue end before its on-screen line is stale
OVERSTAY_FACTOR = 2.5        # predicted path: this many times its spoken
                             # length...
OVERSTAY_BASE = 3.0          # ...plus this, on screen, before a line with no
                             # track counts as stale -- the ×length term keeps
                             # a long sentence from being cut mid-speech
OVERSTAY_ABS_CAP = 30.0      # no track, no playing signal: a hotspot lingering
                             # this long is a stuck read, not a real caption


def overstayed(age, words, rate=None, paused=None, play_time=None,
               cue_end=None):
    """Whether a caption on screen for `age` seconds has outlived its speech
    and should be force-cleared. `words` is its word count, `rate` this video's
    measured seconds-per-word (None -> the default), `paused`/`play_time` the
    browser's reported state (paused is three-valued: True/False/None), and
    `cue_end` the caption track's end (video seconds) for this line when it
    confidently matched, else None.

    The track cue end is the precise signal; the word-count prediction is the
    fallback for videos with no track. Both are gated so a legitimately long
    line is never cut mid-speech, and neither fires while paused."""
    if age < MIN_OVERSTAY:
        return False
    if paused is True:
        return False                     # a frozen frame holds its caption
    if cue_end is not None and play_time is not None:
        return play_time > cue_end + TRACK_OVERSTAY_MARGIN
    if paused is False and words:
        # Predict only when the bridge says we are DEFINITELY playing: a
        # paused or unknown state must not race a legitimately long line.
        return age > spoken_duration(words, rate) * OVERSTAY_FACTOR \
            + OVERSTAY_BASE
    return age > OVERSTAY_ABS_CAP


def set_clip_bounds(min_clip=None, max_clip=None, auto=None):
    """Apply the user's clip-length settings process-wide. MIN_CLIP,
    MAX_CLIP and AUTO_CLIP are module globals that every window function
    reads at call time, so a settings change takes effect on the next
    card."""
    global MIN_CLIP, MAX_CLIP, AUTO_CLIP
    if min_clip:
        MIN_CLIP = float(min_clip)
    if max_clip:
        MAX_CLIP = float(max_clip)
    if auto is not None:
        AUTO_CLIP = bool(auto)


def max_clip():
    """The cap in force: the Auto ceiling when Auto length is on (the clip
    fits the sentence), the user's slider otherwise."""
    return AUTO_MAX_CLIP if AUTO_CLIP else MAX_CLIP

def audio_window(sentence, now):
    """Return the monotonic window to cut from the loopback buffer."""
    appeared = getattr(sentence, "appeared_at", 0.0) or 0.0
    cleared = getattr(sentence, "cleared_at", 0.0) or 0.0
    if appeared <= 0.0:
        # Nothing to anchor on: centre the full cap on the click itself,
        # so the word just heard sits in the MIDDLE, not at the very end
        # (centred, not end-weighted: the video may be paused under the
        # open popup). The recorder waits out the post-click half.
        half = max_clip() / 2.0
        return now - half, now + half

    t0 = appeared - APPEAR_LAG - PREROLL
    if cleared > 0.0:
        t1 = cleared - CLEAR_LAG + POSTROLL
    else:
        t1 = now

    if t1 - t0 > max_clip():
        t1 = t0 + max_clip()
    return widen_to_min(t0, t1)


def shrink_to_max(t0, t1, max_len=None, center=None):
    """Cap [t0, t1] at max_len (the cap in force — max_clip() — when None),
    keeping the stretch around `center` (clamped into the window; the
    midpoint when None) — the inverse of widen_to_min. A long caption cue is
    still one sentence, but a card only needs the clicked word's moment, and
    the playback position at click time is the best guess for where that
    moment is."""
    if max_len is None:
        max_len = max_clip()
    if t1 - t0 <= max_len:
        return t0, t1
    if center is None:
        center = (t0 + t1) / 2.0
    else:
        center = min(max(center, t0), t1)
    half = max_len / 2.0
    n0, n1 = center - half, center + half
    if n0 < t0:          # keep the cap inside the original window
        n1 += t0 - n0
        n0 = t0
    elif n1 > t1:
        n0 -= n1 - t1
        n1 = t1
    return n0, n1


def widen_to_min(t0, t1, min_len=None, floor=None):
    """Widen [t0, t1] symmetrically until it lasts `min_len` (MIN_CLIP as
    set by the user when None), keeping the original midpoint — a one-word
    caption otherwise cuts a blip too short to hear. `floor` clamps the
    start (video time can't go below 0); what the clamp eats is added to
    the end instead."""
    if min_len is None:
        min_len = MIN_CLIP
    lack = min_len - (t1 - t0)
    if lack > 0.0:
        t0 -= lack / 2.0
        t1 += lack / 2.0
    if floor is not None and t0 < floor:
        t1 += floor - t0
        t0 = floor
    return t0, t1
