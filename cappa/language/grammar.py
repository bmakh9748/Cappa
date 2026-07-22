"""The word's anatomy as rich text — the popup's Grammar tab AND the card's
Breakdown field draw from here, so the two never tell different stories.

anatomy_html(surface, lemma) dispatches on the video's language
(translate.SOURCE_LANGUAGE): Japanese = the inflection chain explained plus a
per-kanji breakdown; Arabic = root, verb form (I–X with its pattern), lemma and
gloss; Indonesian = the root under the affixes, each affix explained. Returns
"" when the language has nothing to teach about the word.

Pure string builders — no widgets, no network beyond what the language packs
already do on the calling thread. No Qt (so the flashcard builder can call it
too)."""

import html

from . import arabic, indonesian
from . import translate as translate_mod
from .japanese import jmdict, kanjidic


def _dim(text):
    return '<span style="color:#8a8fa2">%s</span>' % text


def _para(inner):
    return '<p style="margin:0 0 7px 0">%s</p>' % inner


def anatomy_html(surface, lemma):
    """The word's anatomy for the video's language, or "" when there's
    nothing to teach: Japanese = the inflection chain explained + a per-kanji
    breakdown; Arabic = root, form (with its pattern one-liner), lemma and
    gloss; Indonesian = the root under the affixes, each affix explained."""
    lang = translate_mod.SOURCE_LANGUAGE
    if lang == jmdict.LANG:
        return _japanese(surface, lemma)
    if lang == arabic.LANG:
        return _arabic(surface)
    if lang == indonesian.LANG:
        return _indonesian(surface)
    return ""


def _japanese(surface, lemma):
    parts = []
    match = jmdict.word_at(surface, 0) if surface else None
    # Same agreement test as the Meaning tab's inflection line: the match
    # must cover the whole surface AND resolve to the lemma the overlay
    # committed — two views must never tell two stories about one word.
    covers = (match is not None and match.end == len(surface)
              and (not lemma or match.base == lemma))
    base = match.base if covers else (lemma or surface)
    if covers and match.reasons:
        steps = []
        for reason in match.reasons:
            note = jmdict.GRAMMAR_NOTES.get(reason)
            steps.append("<b>%s</b> — %s" % (html.escape(reason),
                                             html.escape(note))
                         if note else html.escape(reason))
        parts.append(_para("%s → %s<br>%s" % (
            html.escape(surface), html.escape(base), "<br>".join(steps))))
    for k in kanjidic.breakdown(base):
        meta = []
        if k.strokes:
            meta.append("%d strokes" % k.strokes)
        if k.grade:
            meta.append("Grade %d" % k.grade if k.grade <= 6
                        else ("Jōyō" if k.grade == 8 else "Names"))
        if k.jlpt:
            meta.append("JLPT %d (old)" % k.jlpt)
        readings = []
        if k.onyomi:
            readings.append("On " + "・".join(k.onyomi[:3]))
        if k.kunyomi:
            # '.' splits stem/okurigana and '-' marks suffix position in
            # KANJIDIC; neither reads well raw.
            readings.append("Kun " + "・".join(
                r.lstrip("-").replace(".", "") for r in k.kunyomi[:4]))
        line = "<b>%s</b>&nbsp; %s" % (
            html.escape(k.literal), html.escape("; ".join(k.meanings[:4])))
        for extra in (" · ".join(readings), " · ".join(meta)):
            if extra:
                line += "<br>" + _dim(html.escape(extra))
        parts.append(_para(line))
    if not parts and not kanjidic.ready():
        # No chain and no kanji rows BECAUSE the pack isn't here yet —
        # say so instead of an implausible "no notes" (rule 7).
        return _para(_dim("Kanji pack not downloaded yet — it fetches in "
                          "the background on a Japanese video."))
    return "".join(parts)


def _arabic(surface):
    analysis = arabic.analyze(surface)
    if analysis is None:
        # Unknown word, or no machinery at all? The difference must be
        # visible (rule 7) — status() is settled now that analyze() ran.
        reason = arabic.status()
        return _para(_dim(html.escape(reason))) if reason else ""
    head = []
    if analysis.loan:
        head.append(_dim("Loanword — no Arabic root"))
    elif analysis.root:
        head.append("Root: <b>%s</b>" % html.escape(analysis.root))
    lemma_line = html.escape(analysis.lemma)
    if analysis.pos:
        lemma_line += " " + _dim("(%s)" % html.escape(analysis.pos))
    head.append(lemma_line)
    if analysis.gloss:
        head.append(_dim(html.escape(analysis.gloss)))
    parts = [_para("<br>".join(head))]
    if analysis.form:
        note = arabic.form_note(analysis.form)
        if note:
            pattern, translit, text = note
            parts.append(_para("Form %s — %s <i>%s</i><br>%s" % (
                html.escape(analysis.form), html.escape(pattern),
                html.escape(translit), _dim(html.escape(text)))))
        else:
            parts.append(_para("Form %s" % html.escape(analysis.form)))
    return "".join(parts)


def _indonesian(surface):
    got = indonesian.anatomy(surface)
    if got is None:
        reason = indonesian.status()   # settled by the anatomy() attempt
        return _para(_dim(html.escape(reason))) if reason else ""
    stem, labels = got
    notes = dict(indonesian.AFFIX_NOTES)
    parts = [_para("Root: <b>%s</b>" % html.escape(stem))]
    for label in labels:
        note = notes.get(label)
        if note:
            parts.append(_para("<b>%s</b> — %s" % (
                html.escape(label), _dim(html.escape(note)))))
    return "".join(parts)
