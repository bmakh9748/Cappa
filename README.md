# Cappa

Transparent screen overlay that detects burned-in subtitles from any video and turns them into Anki flashcards. It teaches **into English**, from three languages — **Arabic, Indonesian and Japanese** — a deliberate focus so each one is deep (dictionary, examples, grammar anatomy, audio) rather than fourteen being shallow.

## Run

```bash
pip install -r requirements.txt   # into a venv; also needs the ffmpeg binary on PATH
pip install --no-deps camel-tools # the Arabic analyzer, slim (see requirements.txt)
python run.py                     # or: python -m cappa
```

On launch a small startup window shows the settings in two tabs — *Languages* (video language, translation language) and *Flashcards* (what each card collects — word/sentence translations, screenshot, audio and its clip length — and whether it goes on the card's front or back, or off entirely); set them and click *Start Cappa*.

**Japanese:** Japanese writes no spaces, so Cappa makes every *character* clickable and lets the dictionary decide where the word ends — click anywhere in 戻るのも and you get 戻る, "Godan verb, intransitive", with its senses; click 食べられなかった and the popup shows 食べられる and how the inflection got there. If it picks the wrong span, **drag across the characters** you want — down to a single character — and the definition updates live as you highlight (nothing is saved until you let go); what you picked stays highlighted while its popup is open. It's offline: the first Japanese video downloads a JMdict pack (14 MB, definitions *and* example sentences) in the background, and no lookup ever leaves your machine.

**Controls:** everything is behind the small icon at the bottom-left of the screen — click it for a menu: *Pick window* (click a window to lock onto it) · *Select area* (drag a box over just the video/subtitle region, then resize it any time by dragging its border) · *Use video from clipboard* (paste a YouTube URL for exact caption timing/audio — or install `extension/` so it's automatic) · *Settings…* (reopens the startup window) · *Exit*. Hover the icon for status (target · fps · captions · yt). Hovering a detected caption word underlines it; **click it** for its popup: the word with a 🔊 that pronounces it, a *Meaning* tab (definitions), an *Examples* tab (the word inside real sentences, with translations — loaded only when you open the tab), and a *Grammar* tab — the word's anatomy: Japanese gets each inflection step explained plus a per-kanji breakdown (meanings, readings, strokes, grade, JLPT); Arabic gets the root, the verb form (I–X, with its pattern), the vocalized lemma and a gloss; Indonesian gets the root under the affixes with each affix explained. ✕ closes it — clicking a word never clicks the video underneath. **Ctrl+Alt+Shift+X** quits from anywhere (also shown next to Exit in the menu). Esc only cancels a pending pick/drag.

**Flashcards:** clicking **Create Anki card** in the word popup gathers the card and shows you a **preview** of it — every piece it will carry (word, both translations, the sentence, the screenshot, the audio clip you can play), laid out on the front and back faces your Flashcards settings chose, with any notes about what degraded. Nothing has reached Anki yet: *Add to Anki* delivers it (live into the running app via AnkiConnect, or straight into the collection file when Anki is closed — no export button, no import dialog), and *Discard* throws the draft away. Preview only for now; editing a bad OCR read comes next.

## Layout

| Path | Role |
|---|---|
| `run.py` | Launcher |
| `cappa/app.py` | Qt setup + `main()`: startup window → overlay + launcher |
| `cappa/winapi.py` | All Win32/DWM calls (no Qt) |
| `cappa/settings.py` | Persisted user settings (`settings.json`) |
| `cappa/language/translate.py` | Sentence/word translation — deep-translator's free Google endpoint, **never an LLM** |
| `cappa/language/dictionary.py` | Word meanings — Wiktionary definitions, Google as hint + fallback |
| `cappa/language/examples.py` | Example sentences for the popup's Examples tab — JMdict pack (Japanese, offline), Wiktionary, Tatoeba |
| `cappa/language/pronounce.py` | The popup's 🔊 — free Google TTS fetch, played through Windows (winmm) |
| `cappa/arabic.py` | Arabic anatomy — root, verb form, lemma, gloss (slim CAMeL Tools + its offline morphology pack) |
| `cappa/indonesian.py` | Indonesian anatomy — Sastrawi root + affix identification |
| `cappa/language/japanese/kanjidic.py` | Per-kanji info — KANJIDIC2 pack (meanings, readings, strokes, grade, JLPT) |
| `cappa/grammar_notes.py` | The Grammar tab's hand-written one-liners (Japanese inflection reasons, Arabic Forms I–X, Indonesian affixes) |
| `cappa/language/japanese/jmdict.py` | Japanese word lookup — JMdict + deinflection, offline. Finds where the word ends, which nothing at OCR time can know |
| `cappa/audio.py` | WASAPI loopback ring buffer (record what you hear, clip retroactively) |
| `cappa/ui/` | Everything you see: the overlay, corner launcher, settings window, word popup, card preview |
| `cappa/detection/` | Everything that finds captions, one stage per file — capture → diff → **neural text detection** (PP-OCRv5 via ONNX) → OCR → clear-watching — on a background thread; every text line found becomes hoverable words |
| `cappa/source/` | Video-source truth: Cappa's own transcript times the clips; VTT parsing + OCR-line alignment (text provenance), yt-dlp/ffmpeg, browser bridge |
| `cappa/flashcard/` | A clicked word → a card draft folder under `cards/card_NNNN/` (audio window choice, screenshot, provenance); the preview then delivers it to Anki (`anki_sync.py` — live via AnkiConnect when Anki is open, into its collection file when closed) or deletes it |
| `extension/` | "Cappa Bridge" browser extension: which video + position → the bridge |

Every package's `__init__.py` docstring holds its per-file map — read those first. [AGENTS.md](AGENTS.md) is the structural rulebook for anyone (human or LLM) changing the code.

## Attribution

Japanese word lookup uses **JMdict**, a property of the [Electronic Dictionary Research and Development Group](https://www.edrdg.org/), used in conformance with the Group's [licence](https://www.edrdg.org/edrdg/licence.html). The pack is built from the [jmdict-simplified](https://github.com/scriptin/jmdict-simplified) JSON release (the examples variant) and downloaded at runtime; it is not redistributed with this repository.

Example sentences come from **[Tatoeba](https://tatoeba.org/)** ([CC BY 2.0 FR](https://creativecommons.org/licenses/by/2.0/fr/)) — both the pairs inside the JMdict pack and the live top-up the popup fetches — and from the words' own [Wiktionary](https://en.wiktionary.org/) entries; the popup credits the source under the sentences.

Per-kanji information uses **KANJIDIC2**, also the EDRDG's ([KANJIDIC Project](https://www.edrdg.org/wiki/index.php/KANJIDIC_Project), CC BY-SA 4.0), built from the same [jmdict-simplified](https://github.com/scriptin/jmdict-simplified) releases.

Arabic morphology uses **[CAMeL Tools](https://github.com/CAMeL-Lab/camel_tools)** (MIT) with the **calima-msa-r13** morphology database ([CAMeL Lab](https://github.com/CAMeL-Lab/camel-tools-data), GPL-2, derived from the Buckwalter analyzer), downloaded at runtime and not redistributed here.

Indonesian stemming uses **[Sastrawi](https://github.com/har07/PySastrawi)** (MIT); its bundled root list derives from [Kateglo](https://github.com/ivanlanin/kateglo) (CC BY-NC-SA 3.0).

## Tests

```bash
python tests/run_all.py       # the whole suite: units first, then live tests
python tests/test_diff.py     # ...or any single file
```

The unit tests are fast and windowless (a few download a dictionary pack
once, and skip cleanly offline). The live tests open
small always-on-top windows, load the neural model (a few seconds each) and
drive the real pipeline against real on-screen pixels — **hands off the mouse
and keyboard while they run**, anything covering their windows changes what
they see. The two simulator tests draw cyan outlines around whatever
detection accepts, so you can watch it work.

See [PLAN.md](PLAN.md) for the full architecture and build order.
