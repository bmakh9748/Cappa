"""Caption detection — everything that finds captions on screen lives here.

The map (one stage per file, chained by worker.py on a background thread):

    capture.py     screen grab (mss)                  every frame   ~10 ms
    diff.py        what changed since last frame      every frame   <1 ms
    stability.py   watch live captions for vanishing  every frame   <1 ms
    detector.py    NEURAL text detection (ONNX)       on change     ~0.06-0.1 s
    ocr.py         read text in accepted boxes (ONNX) on accept     ~0.02 s
                   (one hotspot per WORD; per CHARACTER on CJK lines)
    tracking.py    ledger: live boxes, clear debounce, drift
    classifier.py  the junk-text tag (clock/URL/handle stays off cards)
    sentence.py    the data model a read line becomes: a Sentence of Words.
                   A CJK Word is one character -- nothing here knows where a
                   Japanese word ends, so cappa.jmdict resolves it at lookup
                   time and span_word() fuses the range back into one Word
    latency.py     the pipeline's measured reaction times (appear/clear lags)
    worker.py      the background thread gluing it all together

Every stage except worker.py is Qt-free and testable in isolation. The UI
talks to this package only through CaptureWorker's Qt signals (imported from
.worker directly — this __init__ stays import-light so other packages can
reach sentence.py/latency.py without pulling in Qt or the capture stack):
`regions` carries ("appeared"/"cleared", box) events plus the live caption
boxes; `fps` the capture rate."""
