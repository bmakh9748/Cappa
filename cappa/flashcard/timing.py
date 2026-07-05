"""Audio timing for flashcard drafts."""

# Detection latency between a real on-screen event and our timestamp for it.
APPEAR_LAG = 0.30   # settle debounce (~0.1s) + up to one scan interval
CLEAR_LAG = 0.35    # tracking.CLEAR_CONFIRM before a vanish is surfaced

# Safety padding so a word at the very edge of the line still has its audio.
PREROLL = 0.40
POSTROLL = 0.40
MAX_CLIP = 12.0      # cap when the line is still on screen
FALLBACK_CLIP = 6.0  # window ending "now" when no appear timestamp exists


def audio_window(sentence, now):
    """Return the monotonic window to cut from the loopback buffer."""
    appeared = getattr(sentence, "appeared_at", 0.0) or 0.0
    cleared = getattr(sentence, "cleared_at", 0.0) or 0.0
    if appeared <= 0.0:
        return now - FALLBACK_CLIP, now

    t0 = appeared - APPEAR_LAG - PREROLL
    if cleared > 0.0:
        t1 = cleared - CLEAR_LAG + POSTROLL
    else:
        t1 = now

    if t1 - t0 > MAX_CLIP:
        t1 = t0 + MAX_CLIP
    if t1 <= t0:
        t1 = t0 + 0.5
    return t0, t1
