"""Detection's measured reaction times, for anyone timing against its stamps.

The pipeline stamps a caption row's appear/clear moments with
time.monotonic(), but each stamp trails the real on-screen event by the
pipeline's own reaction time. Consumers that turn stamps back into real
moments (the flashcard audio window, the OCR transcript log) subtract these.
They describe DETECTION — the diff's settle debounce, the scan cadence, the
clear confirm — so they live here, next to the stages that cause them; the
consuming packages import them rather than guessing.

Plain constants, no imports, no Qt: safe for any package to import."""

# Between a caption really appearing and our stamp for it: the settle
# debounce (~0.1s) plus up to one scan interval.
APPEAR_LAG = 0.30
# Between a caption really vanishing and the surfaced clear:
# tracking.CLEAR_CONFIRM must pass before a vanish is trusted.
CLEAR_LAG = 0.35
