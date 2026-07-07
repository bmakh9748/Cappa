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

Cost discipline: the worker calls read() only for boxes that just passed the
geometric classifier — a few times a minute, never per scan — so the
detection path gets no slower.

Fail-open by design: if the model can't load or a read errors, read()
returns (None, 0.0), and callers must treat unreadable text as NO EVIDENCE,
never as junk. Captions in scripts the model can't read worked before OCR
landed and must keep working."""

import sys

import numpy as np

from .sentence import Sentence

PAD = 4      # px of context around the crop; rec likes breathing room
PAD_ALT = 8  # the second read's padding. Stylised fonts squeeze word
             # spaces below the rec model's column stride, so whether a
             # space is emitted flips with tiny framing changes
             # (card_0056: pad 4 read "GENE"DIKILLER, pad 8 read
             # "GENE"DI KILLER — same frame). Reading twice and letting
             # the SPACIER agreeing read win recovers what one framing
             # swallowed; disagreeing reads keep the first (today's)
             # behaviour.

# settings.source_language code -> rapidocr LangRec value. Only scripts the
# default multi-script model CANNOT read are listed; Latin/CJK languages stay
# on the default, which measured more accurate than their per-language packs.
_SCRIPT_MODELS = {
    "ar": "arabic",
    "ru": "cyrillic",
    "hi": "devanagari",
    "ko": "korean",
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


def _is_cjk(ch):
    o = ord(ch)
    return (0x3040 <= o <= 0x30FF        # hiragana + katakana
            or 0x3400 <= o <= 0x9FFF     # CJK ideographs
            or 0xF900 <= o <= 0xFAFF     # compatibility ideographs
            or 0xFF66 <= o <= 0xFF9F     # halfwidth katakana
            or 0xAC00 <= o <= 0xD7AF)    # hangul


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
    if len(spans) < 2 or any(_is_cjk(ch) for ch in text):
        return text
    joined = " ".join(t for t, _ in spans)
    if joined != text and joined.replace(" ", "") == text.replace(" ", ""):
        return joined
    return text


def _is_rtl(ch):
    o = ord(ch)
    return (0x0590 <= o <= 0x08FF        # Hebrew, Arabic + supplements
            or 0xFB1D <= o <= 0xFDFF     # presentation forms A
            or 0xFE70 <= o <= 0xFEFF)    # presentation forms B


class TextReader:
    def __init__(self, lang=None):
        self._model = None
        self._failed = False
        self._script = _SCRIPT_MODELS.get(lang)  # None -> default model

    def set_language(self, lang):
        """Switch the rec model for a newly picked video language (worker
        thread, between scans). A no-op unless the language maps to a
        different model; otherwise the current model is dropped and the next
        read loads the right one."""
        script = _SCRIPT_MODELS.get(lang)
        if script == self._script:
            return
        self._script = script
        self._model = None
        self._failed = False  # a new model deserves a fresh load attempt

    def warm(self):
        """Load the model now (worker calls this at thread start)."""
        self._ensure()

    @property
    def ready(self):
        return self._model is not None

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
        if got is None:
            return None, 0.0
        text, score, spans = got
        if text and not any(_is_cjk(ch) for ch in text):
            # Second framing: the spacier read wins when both saw the same
            # characters (PAD_ALT above; never CJK — spacelessness is
            # correct there and a hallucinated split must not stick).
            alt = self._read_once(frame, box, PAD_ALT)
            if (alt is not None and alt[0]
                    and alt[0].replace(" ", "") == text.replace(" ", "")
                    and alt[0].count(" ") > text.count(" ")):
                text, score, spans = alt
        if spans:
            text = _respace(text, spans)
        elif text:
            spans = [(text, box)]  # no geometry: the line is one hotspot
        return Sentence(text, box, spans), score

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
        except Exception:
            return None
        if not res.txts:
            return "", 0.0, []
        text = res.txts[0]
        score = float(res.scores[0]) if res.scores else 0.0
        return text, score, self._word_spans(res, cl, cr, l, r, t, b)

    @staticmethod
    def _word_spans(res, cl, cr, l, r, t, b):
        """Partition the line into word spans at the MIDPOINTS between
        adjacent words' edge-character columns. The recogniser emits each
        character at one CTC column, but the emission point drifts within
        the glyph — fixed margins around it made boxes slide into the gaps
        between words. Midpoints instead tile the whole line: every pixel
        belongs to exactly one word, so a hover can never land 'between'
        words or on a shifted box."""
        info = res.word_results[0] if res.word_results else None
        if info is None or not getattr(info, "line_txt_len", 0):
            return []
        unit = (cr - cl) / float(info.line_txt_len)
        groups = [(chars, cols) for chars, cols
                  in zip(info.words, info.word_cols) if chars and cols]
        if not groups:
            return []
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
            }
            if self._script:
                self._model = self._load_script_model(base)
            if self._model is None:
                self._model = RapidOCR(params=base)
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
