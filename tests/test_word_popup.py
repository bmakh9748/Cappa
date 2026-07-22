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

from cappa.language.japanese import jmdict
from cappa.language import translate
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

# ---- a preview of a resolved word renders the same entry a click would --
match = jmdict.word_at(line.text, 0)
collapsed = span_word(line, match.start, match.end)
collapsed.lemma = match.base
popup.preview_for(collapsed, anchor)
assert popup._word_label.text() == "戻る"
assert not popup._anki.isEnabled()
print("PASS: a resolved-word preview renders the entry, still uncommitted")

# ---- a SINGLE character is a legitimate selection ------------------------
# Smaller than the word it sits inside: 倒 dragged out of 面倒くさい gets its
# own entry (or the release-to-translate line), never the whole word's.
line2 = make_line("面倒くさい")
solo = span_word(line2, 1, 2)
m = jmdict.word_at(solo.text, 0)
solo.lemma = m.base if m and m.end == len(solo.text) else None
popup.preview_for(solo, anchor)
# 倒's own entry (JMdict heads it 逆しま, 'reverse; inversion'), NEVER the
# containing word's — the exact headword is the pack's business.
assert popup._word_label.text() != "面倒くさい", popup._word_label.text()
assert "bothersome" not in popup._trans.text(), popup._trans.text()
assert not popup._anki.isEnabled()
print("PASS: a single-character selection previews that character alone")

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

# ======================= the tabs, 🔊, and laziness =======================
import time

from PySide6.QtCore import Qt

from cappa.language import examples as examples_mod
from cappa.language import pronounce


def wait_until(cond, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


# The thread->signal fills land only on a VISIBLE popup (the stale-result
# guards drop the rest). WA_DontShowOnScreen keeps the suite windowless
# while letting isVisible() tell the truth.
parent.setAttribute(Qt.WA_DontShowOnScreen, True)
parent.show()


assert [popup._tabs.tabText(i) for i in range(popup._tabs.count())] \
    == ["Meaning", "Examples", "Grammar"], "tab layout changed"

orig_sentences = examples_mod.sentences
orig_say = pronounce.say
orig_meaning = word_popup.meaning


def exploding_sentences(word, lemma=None, limit=examples_mod.LIMIT):
    raise AssertionError("examples.sentences must not run here")


# ---- Japanese: pack pairs render instantly; the tab tops THIN crops up --
examples_mod.sentences = exploding_sentences
line = make_line("戻るのも面倒なんで")
word = span_word(line, 4, 6)                       # 面倒 — one pack pair
word.lemma = jmdict.word_at(word.text, 0).base
popup.show_for(word, anchor)                       # Meaning tab in front:
assert popup._examples.text(), "pack examples missing from the tab"
assert "面倒" in popup._examples.text()            # …rendered, not fetched
assert "Tatoeba" in popup._exsource.text(), popup._exsource.text()
offline_html = popup._examples.text()
print("PASS: pack examples render at commit, before any tab opens")

# Opening the tab owes a thin crop (<LIMIT pairs) the Tatoeba top-up via
# examples.sentences — which re-includes the pack pairs, so it only grows.
ja_calls = []


def ja_fake(word_text, lemma=None, limit=examples_mod.LIMIT):
    ja_calls.append((word_text, lemma))
    return [examples_mod.Example("面倒なことになった。",
                                 "This got troublesome.",
                                 form="面倒", source="Tatoeba"),
            examples_mod.Example("面倒を見る。", "To look after someone.",
                                 form="面倒", source="Tatoeba")]


examples_mod.sentences = ja_fake
popup._tabs.setCurrentIndex(1)
assert popup.isVisible(), "a tab switch hid the popup (highlight dies)"
# The queued result hasn't landed yet: the pack pairs must still be up,
# not a "Loading…" line that blanks them.
assert popup._examples.text() == offline_html, popup._examples.text()
assert wait_until(lambda: "troublesome" in popup._examples.text())
assert ja_calls == [("面倒", "面倒")], ja_calls
popup._tabs.setCurrentIndex(0)
popup._tabs.setCurrentIndex(1)      # already topped up: no second fetch
assert ja_calls == [("面倒", "面倒")], ja_calls
popup._tabs.setCurrentIndex(0)
print("PASS: a thin pack crop keeps its pairs and tops up once")

# ---- web languages: nothing loads until the tab is actually opened ------
translate.SOURCE_LANGUAGE = "id"
word_popup.meaning = lambda w, s="": "to eat"      # keep _fetch offline
ex_calls = []


def fake_sentences(word, lemma=None, limit=examples_mod.LIMIT):
    ex_calls.append((word, lemma))
    return [examples_mod.Example("Ayo makan!", "Let's eat!", form="makan",
                                 source="Tatoeba")]


examples_mod.sentences = fake_sentences
line = make_line("makan enak")
span = span_word(line, 0, 5)                       # makan, no lemma
popup.preview_for(span, anchor)
assert "Release" in popup._examples.text(), popup._examples.text()
popup.show_for(span, anchor)
assert wait_until(lambda: "to eat" in popup._trans.text())
assert ex_calls == [], "examples fetched with the Meaning tab in front"
popup._tabs.setCurrentIndex(1)
assert wait_until(lambda: "Ayo" in popup._examples.text())
assert ex_calls == [("makan", None)], ex_calls
assert "Ayo <b>makan</b>!" in popup._examples.text(), popup._examples.text()
assert "Let&#x27;s eat!" in popup._examples.text() \
    or "Let's eat!" in popup._examples.text()
assert "Tatoeba (CC BY)" in popup._exsource.text(), popup._exsource.text()
popup._tabs.setCurrentIndex(1)      # already loaded: no second fetch
assert ex_calls == [("makan", None)], ex_calls
popup._tabs.setCurrentIndex(0)
print("PASS: web examples wait for the tab, load once, bold the word")

# ---- the Grammar tab: as lazy as Examples -------------------------------
translate.SOURCE_LANGUAGE = "ja"
gr_calls = []
orig_grammar_html = word_popup._grammar_html


def fake_grammar_html(surface, lemma):
    gr_calls.append((surface, lemma))
    return "<p><b>fake grammar</b></p>"


word_popup._grammar_html = fake_grammar_html
word = span_word(make_line("戻るのも面倒なんで"), 4, 6)
word.lemma = jmdict.word_at(word.text, 0).base
popup.show_for(word, anchor)              # Meaning tab in front
assert gr_calls == [], "grammar built with the Meaning tab in front"
popup._tabs.setCurrentIndex(2)
assert popup.isVisible(), "a tab switch hid the popup"
assert wait_until(lambda: "fake grammar" in popup._grammar.text())
assert gr_calls == [("面倒", "面倒")], gr_calls
popup._tabs.setCurrentIndex(0)
popup._tabs.setCurrentIndex(2)            # latched: no rebuild
assert gr_calls == [("面倒", "面倒")], gr_calls
# A drag preview shows the placeholder and never computes.
popup.preview_for(span_word(make_line("makan enak"), 0, 5), anchor)
assert "Release" in popup._grammar.text(), popup._grammar.text()
assert gr_calls == [("面倒", "面倒")], gr_calls
popup._tabs.setCurrentIndex(0)
word_popup._grammar_html = orig_grammar_html
print("PASS: the Grammar tab waits for its turn, builds once")

# ---- 🔊 speaks the shown headword off-thread ----------------------------
translate.SOURCE_LANGUAGE = "ja"
say_calls = []


def fake_say(text, lang, still_wanted=None):
    say_calls.append((text, lang))


pronounce.say = fake_say
word = span_word(make_line("戻るのも面倒なんで"), 4, 6)
word.lemma = jmdict.word_at(word.text, 0).base
popup.show_for(word, anchor)
assert popup._speak.isEnabled(), "🔊 dead with a named language"
popup._speak.click()
assert wait_until(lambda: popup._speak.isEnabled())
assert say_calls == [("面倒", "ja")], say_calls

# Auto-detect has no voice: the button disarms instead of failing.
translate.SOURCE_LANGUAGE = "auto"
popup.preview_for(span_word(make_line("makan enak"), 0, 5), anchor)
assert not popup._speak.isEnabled(), "🔊 armed with no language named"
print("PASS: 🔊 speaks the headword once; auto-detect disarms it")

examples_mod.sentences = orig_sentences
pronounce.say = orig_say
word_popup.meaning = orig_meaning
translate.SOURCE_LANGUAGE = "ja"

print("\nALL PASS")
