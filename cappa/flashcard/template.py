"""Anki-style card templates: HTML faces + shared CSS.

Anki cards are not LaTeX -- a card face is an HTML fragment whose
{{Field}} mustache tags are filled in from the note, with one CSS sheet
styling both faces (Anki adds .nightMode on the root in dark mode, which
the default styling honours). {{#Field}}...{{/Field}} blocks render only
when the field is non-empty, so a card that lost its screenshot or audio
leaves no blank row behind.

default_template() GENERATES the default design from the user's field
placements: flip a field to the other side (or off) in the settings and
the default follows. A custom template saved in the advanced editor is
used verbatim instead (cappa.flashcard.prefs decides which one applies).
anki_sync.sync() keeps the Anki notetype in step with this design -- it
creates the notetype the first time a card needs it and, on later syncs,
adds any field the design has gained and refreshes the faces/CSS to match,
so turning a field on really does make it appear on the card."""

import re

from ..settings import CARD_BACK, CARD_FIELDS, CARD_FRONT, CARD_OFF

# Our field keys -> the Anki note field names the mustache tags use.
ANKI_FIELD_NAMES = {
    "word": "Word",
    "word_translation": "Word Translation",
    "sentence": "Sentence",
    "sentence_translation": "Sentence Translation",
    "screenshot": "Screenshot",
    "audio": "Audio",
    "breakdown": "Breakdown",
    "word_audio": "Word Audio",
}

# One HTML snippet per field, wrapped in a conditional so a card missing
# that piece (screenshot failed, audio was silent...) shows nothing there.
_SNIPPETS = {
    key: '{{#%(f)s}}<div class="%(cls)s">{{%(f)s}}</div>{{/%(f)s}}' % {
        "f": name, "cls": key.replace("_", "-")}
    for key, name in ANKI_FIELD_NAMES.items()
}

_CSS = """\
.card {
  font-family: "Segoe UI", "Noto Sans", sans-serif;
  font-size: 20px;
  text-align: center;
  padding: 24px 16px;
  color: #1d2029;
  background: #fafbfd;
}
.word {
  font-size: 34px;
  font-weight: 700;
}
.sentence {
  font-size: 22px;
  line-height: 1.45;
  margin-top: 12px;
}
.screenshot img {
  max-width: 92%;
  margin-top: 14px;
  border-radius: 8px;
}
.audio { margin-top: 10px; }
.word-audio { margin-top: 10px; }
.breakdown {
  font-size: 15px;
  line-height: 1.4;
  text-align: left;
  margin: 14px auto 0;
  max-width: 30em;
  color: #3a3f4c;
}
.breakdown b { color: #1d2029; }
hr#answer {
  border: none;
  border-top: 1px solid #c9cdd8;
  margin: 18px 8%;
}
.word-translation {
  font-size: 26px;
  font-weight: 600;
  color: #1668a8;
}
.sentence-translation {
  font-size: 19px;
  line-height: 1.45;
  color: #4b5162;
  margin-top: 10px;
}
.nightMode .card {
  color: #eaeaf0;
  background: #12141c;
}
.nightMode .word-translation { color: #5ad2ff; }
.nightMode .sentence-translation { color: #9aa0b4; }
.nightMode .breakdown { color: #c6cad8; }
.nightMode .breakdown b { color: #eaeaf0; }
.nightMode hr#answer { border-top-color: #3a3f52; }
"""


def _mentions(html, name):
    """Whether a face's HTML uses this Anki field: any {{...Name}} tag --
    plain, conditional (#/^//) or filtered (type:Name). Anchored at the
    closing braces so "Word" never matches inside "Word Translation"."""
    return re.search(
        r"\{\{[#/^]?\s*(?:[\w-]+:)*\s*%s\s*\}\}" % re.escape(name),
        html) is not None


def infer_placements(template):
    """Read a design back into field placements: a field mentioned on the
    front face is front, else mentioned on the back is back, else off.
    This is what lets a hand-edited design drive the simple settings rows
    (and with them what gets GATHERED): deleting {{Audio}} from a custom
    design really does stop audio being recorded."""
    front = template.get("front", "") or ""
    back = template.get("back", "") or ""
    placements = {}
    for key, name in ANKI_FIELD_NAMES.items():
        if _mentions(front, name):
            placements[key] = CARD_FRONT
        elif _mentions(back, name):
            placements[key] = CARD_BACK
        else:
            placements[key] = CARD_OFF
    return placements


def default_template(placements):
    """The default {"front", "back", "css"} design for a placement mapping
    (field key -> "front"/"back"/"off"), fields in CARD_FIELDS order. The
    back opens with Anki's {{FrontSide}} + answer divider, the standard
    question-then-answer reveal."""
    front = [_SNIPPETS[key] for key, _, _ in CARD_FIELDS
             if placements.get(key) == CARD_FRONT]
    back = [_SNIPPETS[key] for key, _, _ in CARD_FIELDS
            if placements.get(key) == CARD_BACK]
    if front:
        back = ["{{FrontSide}}", '<hr id="answer">'] + back
    return {
        "front": "\n".join(front),
        "back": "\n".join(back),
        "css": _CSS,
    }
