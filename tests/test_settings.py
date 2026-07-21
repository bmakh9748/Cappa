"""Unit test: settings load/save roundtrip and the translate target switch.
Windowless and network-free (translate.set_target_language only touches module
state; no translation call is made)."""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cappa.settings as S
from cappa import translate


def main():
    original_path = S._PATH
    with tempfile.TemporaryDirectory() as d:
        S._PATH = os.path.join(d, "settings.json")
        try:
            # Missing file -> defaults.
            d0 = S.load()
            assert d0.target_language == S.DEFAULT_TARGET
            assert d0.source_language == S.DEFAULT_SOURCE

            # Roundtrip valid choices for both. The roster is reduced (en
            # target; ar/id/ja sources), so the roundtrip uses a non-default
            # source to prove it really persisted.
            S.save(S.Settings("en", "ar"))
            got = S.load()
            assert got.target_language == "en" and got.source_language == "ar"
            # A pre-reduction settings.json (dropped codes) falls back.
            with open(S._PATH, "w", encoding="utf-8") as f:
                json.dump({"target_language": "fr", "source_language": "es"},
                          f)
            old = S.load()
            assert old.target_language == S.DEFAULT_TARGET
            assert old.source_language == S.DEFAULT_SOURCE
            print("PASS settings: save/load roundtrip (target + source)")

            # Unknown codes fall back independently.
            with open(S._PATH, "w", encoding="utf-8") as f:
                json.dump({"target_language": "zzz", "source_language": "qqq"}, f)
            bad = S.load()
            assert bad.target_language == S.DEFAULT_TARGET
            assert bad.source_language == S.DEFAULT_SOURCE
            print("PASS settings: unknown codes fall back to defaults")

            # Clip bounds roundtrip; out-of-range/garbage values are clamped
            # to the slider range or fall back to the defaults.
            S.save(S.Settings(min_clip_seconds=0.7, max_clip_seconds=4.5))
            got = S.load()
            assert got.min_clip_seconds == 0.7, got.min_clip_seconds
            assert got.max_clip_seconds == 4.5, got.max_clip_seconds
            with open(S._PATH, "w", encoding="utf-8") as f:
                json.dump({"min_clip_seconds": 99, "max_clip_seconds": "x"}, f)
            got = S.load()
            assert got.min_clip_seconds == S.MIN_CLIP_RANGE[1]  # clamped
            assert got.max_clip_seconds == S.DEFAULT_MAX_CLIP   # garbage
            # d0 was loaded with no file at all: defaults.
            assert d0.min_clip_seconds == S.DEFAULT_MIN_CLIP
            assert d0.max_clip_seconds == S.DEFAULT_MAX_CLIP
            print("PASS settings: clip bounds roundtrip, clamp and fallback")

            # Auto clip length: on by default, roundtrips off.
            assert d0.auto_clip is True
            S.save(S.Settings(auto_clip=False))
            assert S.load().auto_clip is False
            S.save(S.Settings(auto_clip=True))
            assert S.load().auto_clip is True
            print("PASS settings: auto clip length defaults on, roundtrips")

            # Card field placements roundtrip; junk is sanitized: unknown
            # keys dropped, bad placements defaulted. Any field may be off,
            # the clicked word included.
            fields = dict(S.DEFAULT_CARD_FIELDS)
            fields["screenshot"] = S.CARD_OFF
            fields["word"] = S.CARD_OFF
            fields["word_translation"] = S.CARD_FRONT
            S.save(S.Settings(card_fields=fields))
            assert S.load().card_fields == fields
            with open(S._PATH, "w", encoding="utf-8") as f:
                json.dump({"card_fields": {"word": "off", "audio": "left",
                                           "bogus": "front"}}, f)
            got = S.load()
            want = dict(S.DEFAULT_CARD_FIELDS)
            want["word"] = S.CARD_OFF
            assert got.card_fields == want, got.card_fields
            with open(S._PATH, "w", encoding="utf-8") as f:
                json.dump({"card_fields": "nonsense"}, f)
            assert S.load().card_fields == S.DEFAULT_CARD_FIELDS
            assert d0.card_fields == S.DEFAULT_CARD_FIELDS
            print("PASS settings: card fields roundtrip and sanitize")

            # Custom card template roundtrip; malformed or all-blank means
            # auto (None), and the default is no template at all.
            custom = {"front": "<b>{{Word}}</b>", "back": "{{FrontSide}}",
                      "css": ""}
            S.save(S.Settings(card_template=custom))
            assert S.load().card_template == custom
            with open(S._PATH, "w", encoding="utf-8") as f:
                json.dump({"card_template": {"front": 5}}, f)
            assert S.load().card_template is None
            assert S.Settings(card_template={"front": "", "back": " ",
                                             "css": ""}).card_template is None
            assert d0.card_template is None
            print("PASS settings: card template roundtrip and sanitize")

            # The Default <-> Custom switch: a stored design can sit unused
            # (flag off) and be switched back on; the flag means nothing
            # without a design to use.
            S.save(S.Settings(card_template=custom, use_custom_template=True))
            got = S.load()
            assert got.use_custom_template is True and got.card_template == (
                custom)
            S.save(S.Settings(card_template=custom, use_custom_template=False))
            got = S.load()
            assert got.use_custom_template is False
            assert got.card_template == custom   # kept while unused
            assert S.Settings(use_custom_template=True
                              ).use_custom_template is False
            assert d0.use_custom_template is False
            print("PASS settings: custom-design switch persists, needs a "
                  "design")
        finally:
            S._PATH = original_path

    # caption_lang maps 'auto' -> None (auto-pick) and passes codes through.
    assert S.caption_lang("auto") is None and S.caption_lang("id") == "id"

    # Switching either language clears the (now-stale) translation cache.
    translate._cache["hola"] = "hello"
    translate.set_target_language("fr")
    assert translate.TARGET_LANGUAGE == "fr" and "hola" not in translate._cache
    translate._cache["bawa"] = "BAWA"
    translate.set_source_language("id")
    assert translate.SOURCE_LANGUAGE == "id", translate.SOURCE_LANGUAGE
    assert "bawa" not in translate._cache, "cache not cleared on source switch"
    translate.set_target_language("en")   # restore for any later test
    translate.set_source_language("auto")
    print("PASS translate: source/target switches apply + clear cache")
    print("ALL PASS")


if __name__ == "__main__":
    main()
