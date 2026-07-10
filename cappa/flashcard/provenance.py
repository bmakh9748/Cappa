"""Sentence provenance checks for clicked words."""

from ..translate import clean_word


def _box_tuple(value):
    if not value:
        return None
    try:
        return tuple(int(v) for v in value)
    except Exception:
        return None


def _box_contains(outer, inner, slack=3):
    if outer is None or inner is None:
        return False
    return (inner[0] >= outer[0] - slack
            and inner[1] >= outer[1] - slack
            and inner[2] <= outer[2] + slack
            and inner[3] <= outer[3] + slack)


def attach_sentence_provenance(word, draft, sentence=None):
    """Record whether the clicked Word belongs to the saved OCR sentence.
    `sentence` is what the card actually saves — the word's own line, or the
    joined CaptionBlock when the caption spanned several lines; it defaults
    to the word's line."""
    if sentence is None:
        sentence = getattr(word, "sentence", None)
    draft.word_box = _box_tuple(getattr(word, "box", None))
    draft.sentence_box = _box_tuple(getattr(sentence, "box", None))
    words = list(getattr(sentence, "words", []) or [])

    # A clicked Word is normally one of the sentence's own. On CJK lines it
    # is not: the hotspots are per character and the clicked word was fused
    # out of a character RANGE (sentence.span_word), so it is matched by the
    # line it came from plus the offset it starts at — which is its first
    # character's hotspot, and lands on the same index the old identity test
    # would have given.
    identity_index = -1
    for i, candidate in enumerate(words):
        if candidate is word:
            identity_index = i
            break
    if identity_index < 0 and getattr(word, "index", -1) >= 0:
        for i, candidate in enumerate(words):
            if (getattr(candidate, "sentence", None)
                    is getattr(word, "sentence", None)
                    and getattr(candidate, "index", -1) == word.index):
                identity_index = i
                break
    draft.word_index = identity_index

    if sentence is None:
        draft.notes.append("sentence missing from clicked word")
        return
    if identity_index < 0:
        draft.notes.append("clicked word is not in its sentence word list")
    if not _box_contains(draft.sentence_box, draft.word_box):
        draft.notes.append("clicked word box is outside the sentence box")

    cleaned = clean_word(getattr(word, "text", "")) or getattr(word, "text", "")
    if cleaned and cleaned.casefold() not in (draft.sentence or "").casefold():
        draft.notes.append("clicked word text not found in OCR sentence")

    draft.sentence_verified = (
        identity_index >= 0
        and _box_contains(draft.sentence_box, draft.word_box)
    )
