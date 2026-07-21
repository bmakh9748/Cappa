"""Step 4, second half: READ the text inside boxes the detector accepted.

Recognition runs a PP-OCR rec model (rapidocr) through onnxruntime — the same
stack as detector.py. The DEFAULT model (PP-OCRv6 small) is multi-script: it
reads Japanese, Chinese, English and the Latin languages with no language
setting, and measured best on all of them — but it cannot read every script.
Arabic came back empty (the user's report), and Cyrillic/Devanagari/Korean are
equally out of its charset. For those, rapidocr ships per-script packs, so the
settings panel's "video language" maps to a rec model here (_SCRIPT_MODELS):
pick Arabic and the arabic pack loads; anything else keeps the measured-best
default. set_language() swaps the model live; loading falls back to the
default pack on any failure.

Cost discipline: the worker calls read() only for boxes the ledger says are
genuinely NEW — a few times a minute, never per scan — so the detection
path gets no slower.

Fail-open by design: if the model can't load or a read errors, read()
returns (None, 0.0), and callers must treat unreadable text as NO
EVIDENCE. Captions in scripts the model can't read worked before OCR
landed and must keep working. Fail-open is never SILENT, though: a raising
read reports itself on stderr, once per failure kind."""

import sys

import numpy as np

from .. import lexicon
from . import gpu
from .sentence import Sentence, is_cjk

PAD = 4      # px of context around the crop; rec likes breathing room

# A det box this much taller than wide is likely a VERTICAL text column
# (Japanese games/titles write top-to-bottom). The rec model reads such a
# column laid on its side almost perfectly and reads it upright as garbage:
# card_0001's real column read 0.31 upright, 0.99 rotated 90° ccw. Both
# reads run and the better score wins, so a tall-but-horizontal box costs
# one wasted rec pass (~10 ms, rare) and can never LOSE accuracy.
VERTICAL_MIN_ASPECT = 1.6

# settings.source_language code -> rapidocr LangRec value. Only scripts the
# default multi-script model CANNOT read are listed; Latin/CJK languages stay
# on the default, which measured more accurate than their per-language packs.
# Of the reduced roster (ar/ja/id) only Arabic needs a script pack.
_SCRIPT_MODELS = {
    "ar": "arabic",
}


def _fix_rtl(word_text):
    """Return `word_text` in logical (reading) order.

    The recogniser emits per-word character groups in CTC column order —
    visual left-to-right — which for right-to-left script is the READING
    ORDER REVERSED. rapidocr repairs the full line's text in its own
    postprocess, but the word_results groups stay raw, so hotspot words came
    out mirrored (تعلمت became تملعت) and neither matched the sentence nor
    translated. Reverse a group back when it's an RTL run; guarded on content,
    not the loaded model, so Latin words (and digit runs, which stay LTR even
    inside Arabic text) are never touched."""
    if not word_text:
        return word_text
    rtl = sum(1 for ch in word_text if _is_rtl(ch))
    if rtl * 2 <= len(word_text):        # not predominantly RTL
        return word_text
    if any(ch.isascii() and ch.isalnum() for ch in word_text):
        return word_text                 # mixed run: leave it untouched
    return word_text[::-1]


def _spaceless(ch):
    """Text whose spacing the recogniser must not be second-guessed on:
    sentence.is_cjk plus hangul. Korean DOES put spaces between words, so
    it never wants per-character hotspots (is_cjk excludes it) — but its
    syllable blocks must never be re-spaced or lexicon-split either."""
    return is_cjk(ch) or 0xAC00 <= ord(ch) <= 0xD7AF


def _respace(text, spans):
    """The rec model sometimes reads a tight stylised line with a space
    missing ('KARENA APA' came back 'KARENAAPA', card_0044) while its word
    grouping still splits the words. When the span texts equal the line
    text ignoring whitespace, the spans are the better segmentation —
    rebuild the text from them. Never for CJK lines: no spaces there is
    correct, and the spans are script-run groups, not words. RTL lines are
    naturally guarded: their spans sit in visual order, so a genuinely
    reordered join fails the ignore-whitespace equality and the raw text
    stays."""
    if len(spans) < 2 or any(_spaceless(ch) for ch in text):
        return text
    joined = " ".join(t for t, _ in spans)
    if joined != text and joined.replace(" ", "") == text.replace(" ", ""):
        return joined
    return text


def _lexicon_split(text, lang):
    """Each word of `text` that the recogniser glued to its neighbour, split
    back apart against `lang`'s word list -- 'YOU CANALWAYS' -> 'YOU CAN
    ALWAYS'. Only pieces that are every one a real word are accepted, so a
    genuine word is never torn and an OCR letter-error (no valid split) is
    left as is. Unchanged when the language has no pack loaded, so this only
    ever adds the ability to split (card_0027)."""
    out = []
    for token in text.split():
        out.extend(lexicon.split(token, lang))
    return " ".join(out)


def _respan(spans, text):
    """Re-cut `spans` (word, box) so they spell `text`, whose words differ
    only in where the spaces fall. A character is assumed to occupy an equal
    slice of its span's box -- the same assumption the midpoint tiling below
    already makes, and accurate enough for a hotspot. Returns the original
    spans when the characters don't line up."""
    words = text.split()
    chars = []
    for word, (l, t, r, b) in spans:
        if not word:
            continue
        step = (r - l) / float(len(word))
        chars += [(l + i * step, l + (i + 1) * step, t, b)
                  for i in range(len(word))]
    if len(chars) != sum(len(w) for w in words):
        return spans
    out, k = [], 0
    for word in words:
        seg, k = chars[k:k + len(word)], k + len(word)
        out.append((word, (int(round(seg[0][0])), min(c[2] for c in seg),
                           int(round(seg[-1][1])), max(c[3] for c in seg))))
    return out


def _is_rtl(ch):
    o = ord(ch)
    return (0x0590 <= o <= 0x08FF        # Hebrew, Arabic + supplements
            or 0xFB1D <= o <= 0xFDFF     # presentation forms A
            or 0xFE70 <= o <= 0xFEFF)    # presentation forms B


class TextReader:
    def __init__(self, lang=None):
        self._model = None
        self._device = None
        self._failed = False
        self._read_errors = set()  # failure kinds already reported, once each
        self._script = _SCRIPT_MODELS.get(lang)  # None -> default model
        self._lang = lang                         # for the lexicon splitter
        # script -> its loaded RapidOCR, kept for the process's life. A
        # replaced model must NEVER be dropped: destroying a RapidOCR
        # instance tears down its ONNX sessions, and on DirectML that
        # poisons every OTHER live session in the process — the next
        # detector scan segfaults the app (reproduced on ORT 1.24.4).
        # Caching also makes switching BACK to a language instant.
        self._models = {}

    def set_language(self, lang):
        """Switch the rec model for a newly picked video language (worker
        thread, between scans). The rec MODEL only changes when the script
        does; the language string is always kept, since the lexicon
        splitter is keyed by language, not script (en and id share a
        model but not a word list). The old model stays cached in
        self._models — see __init__ on why it must never be dropped."""
        self._lang = lang
        script = _SCRIPT_MODELS.get(lang)
        if script == self._script:
            return
        self._script = script
        self._model = self._models.get(script)
        self._device = (gpu.session_device(self._model.text_rec)
                        if self._model is not None else None)
        self._failed = False  # a new model deserves a fresh load attempt
        self._read_errors = set()  # ...and fresh failure reporting

    def warm(self):
        """Load the model now (worker calls this at thread start) — and push
        one dummy crop through it, so a session's first-run cost (DirectML
        compiles on first use) lands here, not on the first caption. read()
        is fail-open, so a raising warm read just reports itself once."""
        self._ensure()
        if self._model is None:
            return
        import numpy as np
        self.read(np.zeros((24, 64, 4), dtype=np.uint8), (2, 2, 62, 22))

    @property
    def ready(self):
        return self._model is not None

    @property
    def device(self):
        """'gpu'/'cpu' once ready — the worker's startup line prints it so
        a silent fall-back to CPU is visible in the console."""
        return self._device

    def read(self, frame, box):
        """frame: (H, W, 4) BGRA uint8, FULL resolution (recognition wants
        the sharpest crop we have). box: (l, t, r, b) full-res px.

        Returns (sentence, confidence): a Sentence whose Words are the
        clickable hotspot units, boxes in FRAME coordinates. For spaced
        scripts a Word is a word; for CJK it is the model's script-run
        grouping (kanji block / kana run) until real tokenisation. When
        word geometry isn't available the whole line is one Word; when
        reading is unavailable entirely: (None, 0.0)."""
        self._ensure()
        if self._model is None:
            return None, 0.0
        l, t, r, b = box
        if b - t < 2 or r - l < 2:  # judged BEFORE padding: a sliver of a
            return None, 0.0        # box is no evidence, however padded
        got = self._read_once(frame, box, PAD)
        if (b - t) >= VERTICAL_MIN_ASPECT * (r - l):
            # Tall column: also try it as VERTICAL text, best read wins
            # (card_0001: 拔山蓋世 written top-to-bottom read upright as
            # '执数单' 0.31 and rotated as itself at 0.99).
            alt = self._read_vertical(frame, box, PAD)
            if alt is not None and (got is None or alt[1] > got[1]):
                got = alt
        if got is None:
            return None, 0.0
        text, score, spans = got
        if spans:
            text = _respace(text, spans)
        elif text:
            spans = [(text, box)]  # no geometry: the line is one hotspot
        if text and not any(_spaceless(ch) for ch in text):
            # A word the recogniser GLUED to its neighbour is split back
            # apart by the lexicon -- but only into pieces that are every
            # one a real word, so a genuine word is never torn and an OCR
            # letter-error is left alone. No pack for the language -> no
            # change. Never CJK: spacelessness there is correct.
            split_text = _lexicon_split(text, self._lang)
            if split_text != text:
                spans = _respan(spans, split_text)
                text = split_text
        sentence = Sentence(text, box, spans)
        sentence.ocr_conf = score  # cards warn when a shaky read got through
        return sentence, score

    def _read_once(self, frame, box, pad):
        """One rec pass over `box` padded by `pad`: (text, score, spans),
        ('', 0.0, []) for a readable-but-empty crop, None on rec failure."""
        h, w = frame.shape[:2]
        l, t, r, b = box
        ct, cb = max(t - pad, 0), min(b + pad, h)
        cl, cr = max(l - pad, 0), min(r + pad, w)
        crop = np.ascontiguousarray(frame[ct:cb, cl:cr, :3])
        try:
            res = self._model(crop, use_det=False, use_cls=False,
                              use_rec=True, return_word_box=True)
        except Exception as exc:
            self._say_read_failed(exc)
            return None
        if not res.txts:
            return "", 0.0, []
        text = res.txts[0]
        score = float(res.scores[0]) if res.scores else 0.0
        return text, score, self._word_spans(res, cl, cr, l, r, t, b)

    def _read_vertical(self, frame, box, pad):
        """One rec pass over `box` as a VERTICAL column: the crop is laid on
        its side (90° ccw — the rotation the model reads; cw reads nothing)
        so the column becomes a left-to-right line in top-to-bottom order,
        and the span geometry is mapped back to upright FRAME boxes stacked
        down the column. Same return contract as _read_once."""
        h, w = frame.shape[:2]
        l, t, r, b = box
        ct, cb = max(t - pad, 0), min(b + pad, h)
        cl, cr = max(l - pad, 0), min(r + pad, w)
        crop = np.ascontiguousarray(np.rot90(frame[ct:cb, cl:cr, :3]))
        try:
            res = self._model(crop, use_det=False, use_cls=False,
                              use_rec=True, return_word_box=True)
        except Exception as exc:
            self._say_read_failed(exc)
            return None
        if not res.txts:
            return "", 0.0, []
        text = res.txts[0]
        score = float(res.scores[0]) if res.scores else 0.0
        # In the rotated crop the reading axis IS the original y axis, so
        # the tiling maths runs with the axes swapped: crop bounds (ct, cb),
        # line bounds (t, b), cross-line bounds (l, r) — then each span box
        # comes back (along0, cross0, along1, cross1) and is swapped into an
        # upright (l, y0, r, y1) cell.
        spans = self._word_spans(res, ct, cb, t, b, l, r)
        return text, score, [
            (word_text, (x0, y0, x1, y1))
            for word_text, (y0, x0, y1, x1) in spans
        ]

    @staticmethod
    def _word_spans(res, cl, cr, l, r, t, b):
        """Partition the line into hotspot spans at the MIDPOINTS between
        adjacent spans' edge-character columns. The recogniser emits each
        character at one CTC column, but the emission point drifts within
        the glyph — fixed margins around it made boxes slide into the gaps
        between words. Midpoints instead tile the whole line: every pixel
        belongs to exactly one span, so a hover can never land 'between'
        them or on a shifted box.

        A span is a WORD for spaced scripts. For CJK it is a single
        CHARACTER: the recogniser's own grouping is by script run, which is
        exactly the okurigana boundary (戻|るのも, 面白|い) — the one place a
        Japanese word never breaks. The word is found later, by dictionary
        lookup from the character under the cursor (cappa.jmdict)."""
        info = res.word_results[0] if res.word_results else None
        if info is None or not getattr(info, "line_txt_len", 0):
            return []
        unit = (cr - cl) / float(info.line_txt_len)
        groups = [(chars, cols) for chars, cols
                  in zip(info.words, info.word_cols) if chars and cols]
        if not groups:
            return []
        if any(is_cjk(ch) for chars, _cols in groups for ch in chars):
            groups = [([ch], [col]) for chars, cols in groups
                      for ch, col in zip(chars, cols)]
        # first/last character centre of each word, in crop-x px
        centres = [((cols[0] + 0.5) * unit, (cols[-1] + 0.5) * unit)
                   for _, cols in groups]
        # The line's OUTER edges come from the character pitch, not the
        # crop: a det box wider than the text must not hand the first/last
        # word a hotspot over empty background.
        deltas = [(cols[i + 1] - cols[i]) * unit
                  for _, cols in groups for i in range(len(cols) - 1)]
        pitch = sorted(deltas)[len(deltas) // 2] if deltas else (b - t)
        # Hotspots stay inside the LINE box: the crop is padded PAD px past
        # it, and an end word clamped to the CROP edge hung over the box by
        # that pad — which failed the card's box-containment provenance.
        lo, hi = float(l - cl), float(r - cl)
        line_l = max(lo, centres[0][0] - 0.7 * pitch)
        line_r = min(hi, centres[-1][1] + 0.7 * pitch)
        spans = []
        for i, (chars, _cols) in enumerate(groups):
            left = line_l if i == 0 else \
                (centres[i - 1][1] + centres[i][0]) / 2
            right = line_r if i == len(groups) - 1 else \
                (centres[i][1] + centres[i + 1][0]) / 2
            left = min(max(left, lo), hi)
            right = min(max(right, lo), hi)
            spans.append((_fix_rtl("".join(chars)),
                          (int(cl + left), t, int(cl + right), b)))
        return spans

    def _say_read_failed(self, exc):
        """A raising read stays fail-open (None -- no evidence, callers keep
        working) but must never be SILENT: rapidocr 3.9 raised
        ModuleNotFoundError (python-bidi, undeclared) on EVERY arabic read,
        and swallowed it looked exactly like 'Arabic came back empty' -- the
        user report the arabic pack exists to fix -- while the reader claimed
        ready. One stderr line per failure kind: a structural failure repeats
        on every read, and one line is signal where a stream is noise."""
        kind = type(exc).__name__
        if kind in self._read_errors:
            return
        self._read_errors.add(kind)
        print("cappa: text read failed (%s: %s) — treating the text as "
              "unreadable" % (kind, exc), file=sys.stderr)

    def _ensure(self):
        if self._model is not None or self._failed:
            return
        try:
            import logging
            logging.getLogger("RapidOCR").setLevel(logging.ERROR)
            from rapidocr import EngineType, RapidOCR
            base = {
                "Global.use_det": False,
                "Global.use_cls": False,
                # Captions are horizontal, so no cls. The default rec model
                # (no lang_type) is the measured-best multi-script pack; a
                # _SCRIPT_MODELS language overrides it below.
                "Rec.engine_type": EngineType.ONNXRUNTIME,
                # GPU when the venv has it (measured a shade faster than
                # CPU, 7 vs 9 ms/read, and it keeps both neural stages on
                # one device); rapidocr falls back to CPU on its own when
                # the provider list disagrees (fail-open).
                "EngineConfig.onnxruntime.use_dml": gpu.available(),
            }
            if self._script:
                self._model = self._load_script_model(base)
            if self._model is None:
                self._model = RapidOCR(params=base)
            self._models[self._script] = self._model  # cached for the
            # process's life — see __init__ on why it must never be dropped
            self._device = gpu.session_device(self._model.text_rec)
        except Exception as exc:  # missing package / download failure
            self._failed = True
            print("cappa: text reading unavailable (%s: %s) — detection "
                  "runs without text rules" % (type(exc).__name__, exc),
                  file=sys.stderr)

    def _load_script_model(self, base):
        """The per-script rec pack for self._script, or None to use the
        default. Script packs don't exist for every OCR version/size (arabic
        is a v5 mobile model, for one), so try the known-good combinations
        and fail open to the default pack with a console note."""
        from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR
        for version in (OCRVersion.PPOCRV5, OCRVersion.PPOCRV4):
            for model_type in (ModelType.MOBILE, ModelType.SERVER):
                params = dict(base)
                params["Rec.lang_type"] = LangRec(self._script)
                params["Rec.ocr_version"] = version
                params["Rec.model_type"] = model_type
                try:
                    model = RapidOCR(params=params)
                except Exception:
                    continue
                print("[cappa] reader: %s pack (%s %s)"
                      % (self._script, version.value, model_type.value))
                return model
        print("cappa: no %r rec pack available — using the default reader"
              % self._script, file=sys.stderr)
        return None
