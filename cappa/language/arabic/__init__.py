"""Arabic: word anatomy for the Grammar tab.

    morphology.py  root/Form I-X/vocalized lemma/gloss via slim camel-tools
                   + the calima-msa-r13 pack (arabic_packs/), plus the
                   Form I-X table (VERB_FORMS) the popup explains X with.

Lazy, fail-soft, no-pack-means-no-change. No Qt."""

from .morphology import (LANG, VERB_FORMS, analyze, ensure_pack, form_note,
                         ready, status, verb_form)
