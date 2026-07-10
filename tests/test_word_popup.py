"""The word popup: the live drag preview vs the committed click.

preview_for() runs on every tick a selection grows, so it must be free:
no screenshot, no playback freeze, no caption snapshot, no network, and a
disabled card button (nothing is committed until the mouse comes up).
show_for() is the commit and does all of it.

Windowless (nothing is shown). The dictionary legs need the JMdict pack and
skip cleanly without it.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication, QWidget

from cappa import jmdict, translate
from cappa.detection.sentence import Sentence, span_word
from cappa.ui import word_popup
from cappa.ui.word_popup import WordPopup

app = QApplication.instance() or QApplication([])


class CountingRegion:
    """Stands in for the overlay's region_provider: proves the preview never
    reaches for a screenshot."""

    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return None


def make_line(text):
    spans = [(ch, (i * 40, 0, i * 40 + 40, 44)) for i, ch in enumerate(text)]
    return Sentence(text, (0, 0, len(text) * 40, 44), spans)


parent = QWidget()
parent.resize(900, 500)
region = CountingRegion()
popup = WordPopup(parent, region_provider=region, captions_provider=lambda: [])
anchor = QRect(40, 300, 60, 44)

if not jmdict.ensure_pack("ja", timeout=180.0):
    print("SKIP: no JMdict pack (offline?)")
    sys.exit(0)
translate.SOURCE_LANGUAGE = "ja"

line = make_line("戻るのも面倒なんで")

# ---- the preview renders the entry, and commits nothing -----------------
word = span_word(line, 0, 2)                       # 戻る, dragged by hand
word.lemma = jmdict.word_at(word.text, 0).base
popup.preview_for(word, anchor)
assert popup.word is None, "preview must not commit a word"
assert not popup._anki.isEnabled(), "card button armed during a preview"
assert popup._word_label.text() == "戻る"
assert "to turn back" in popup._trans.text()
assert "Godan verb" in popup._tags.text()
assert region.calls == 0, "preview captured a screenshot"
assert popup._snapshot_png is None and popup._snapshot_captions == []
print("PASS: preview shows the entry, freezes nothing, arms nothing")

# ---- the selection grows, the definition follows it ---------------------
# A selection is always >= 2 characters: dragging back onto the first one
# collapses it, and the overlay then previews the RESOLVED word instead.
seen = []
for end in (2, 3, 6):     # 戻る -> 戻るの -> 戻るのも面倒
    span = span_word(line, 0, end)
    match = jmdict.word_at(span.text, 0)
    span.lemma = match.base if match and match.end == len(span.text) else None
    popup.preview_for(span, anchor)
    seen.append((span.text, popup._word_label.text(), popup._trans.text()))
assert seen[0][1] == "戻る" and "to turn back" in seen[0][2], seen[0]
# A span the dictionary has no entry for says so, and does NOT translate:
# a lookup per drag tick would be a network call per pixel.
for surface, shown, meaning in seen[1:]:
    assert shown == surface, (shown, surface)
    assert "release to translate" in meaning.lower(), meaning
assert region.calls == 0
print("PASS: the definition tracks the growing selection; unknown spans wait")

# ---- collapsing the selection previews the resolved word again ----------
match = jmdict.word_at(line.text, 0)
collapsed = span_word(line, match.start, match.end)
collapsed.lemma = match.base
popup.preview_for(collapsed, anchor)
assert popup._word_label.text() == "戻る"
assert not popup._anki.isEnabled()
print("PASS: a collapsed selection previews the word under the cursor")

# ---- releasing commits: the word, the button, the frozen moment ---------
word = span_word(line, 4, 6)                       # 面倒
word.lemma = jmdict.word_at(word.text, 0).base
popup.show_for(word, anchor)
assert popup.word is word
assert popup._anki.isEnabled(), "card button not armed after the release"
assert popup._word_label.text() == "面倒"
assert "trouble" in popup._trans.text()
assert region.calls == 1, "the click did not freeze a screenshot region"
print("PASS: show_for commits the word, arms the button, freezes the moment")

# ---- an inflected drag still teaches the dictionary form ----------------
line = make_line("食べられなかった")
span = span_word(line, 0, len(line.text))
match = jmdict.word_at(span.text, 0)
span.lemma = match.base
popup.preview_for(span, anchor)
assert popup._word_label.text() == "食べられる"
assert "past negative" in popup._inflection.text(), popup._inflection.text()
print("PASS: a dragged inflected span previews its dictionary form")

print("\nALL PASS")
