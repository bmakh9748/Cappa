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
            # Missing file -> defaults (target en, source auto).
            d0 = S.load()
            assert d0.target_language == S.DEFAULT_TARGET
            assert d0.source_language == S.DEFAULT_SOURCE

            # Roundtrip valid choices for both.
            S.save(S.Settings("ar", "id"))
            got = S.load()
            assert got.target_language == "ar" and got.source_language == "id"
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
