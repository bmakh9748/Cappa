"""Caption detection — everything that finds captions on screen lives here.

The map (one stage per file, chained by worker.py on a background thread):

    diff.py        screen grab (mss) + what changed   every frame   ~10 ms
                   since the last frame
    stability.py   watch live captions for vanishing  every frame   <1 ms
    detector.py    NEURAL text detection (ONNX)       on change     ~0.04-0.1 s
                   (also decides GPU-via-DirectML vs CPU for both
                   neural stages — fail-open; ocr.py borrows the probe)
    ocr.py         read text in accepted boxes (ONNX) on accept     ~0.01 s
                   (one hotspot per WORD; per CHARACTER on CJK lines; a
                   tall column is also tried as VERTICAL text, best wins)
    tracking.py    ledger: live boxes, clear debounce, drift
    sentence.py    the data model a read line becomes: a Sentence of Words.
                   A CJK Word is one character -- nothing here knows where a
                   Japanese word ends, so cappa.jmdict resolves it at lookup
                   time and span_word() fuses the range back into one Word
    worker.py      the background thread gluing it all together — plus its
                   scan helper thread, so neural scans run BESIDE the loop
                   and frame grabs never pause for one

Every stage except worker.py is Qt-free and testable in isolation. The UI
talks to this package only through CaptureWorker's Qt signals (imported from
.worker directly — this __init__ stays import-light so other packages can
reach sentence.py and the lag constants below without pulling in Qt or the
capture stack): `regions` carries ("appeared"/"cleared", box) events plus
the live caption boxes; `fps` the capture rate."""

# The pipeline's measured reaction times: each appear/clear stamp trails
# the real on-screen event by the pipeline's own reaction, so consumers
# that turn stamps back into real moments (the flashcard audio window, the
# OCR transcript log) subtract these.
APPEAR_LAG = 0.30  # the settle debounce (~0.1s) plus up to one scan interval
CLEAR_LAG = 0.35   # tracking.CLEAR_CONFIRM must pass before a vanish is
                   # trusted
