"""Per-language word lists, and the splitter that uses them.

The OCR recogniser confidently glues neighbouring words on tight stylised
captions ('YOU CAN ALWAYS' read as 'YOU CANALWAYS'), and reading the same
crop at a second padding is only luck -- it helps when the two framings
happen to disagree and each catches a different gap, and it can invent a
split where a framing hallucinates a stray space. The reliable fix is a
dictionary: split a token the recogniser produced ONLY into pieces that are
every one a real word, and leave a token that is already a word alone. A
lexicon decides WHAT to split; ocr._respan positions WHERE the hotspot
boundary lands.

One pack per language, downloaded on first use and cached like the OCR
models (a large frequency-ordered list -- the plan is a user-downloaded
pack per language for the final product). No pack for a language means no
splitting there, exactly as before: this only ever ADDS the ability to
split, never removes a word. CJK is never split (no spaces to recover) and
never has a pack.

Frequency-ordered so the splitter can prefer common words: rank 0 is the
most common word in the language, which is what breaks ties between
otherwise-valid segmentations (a split into two everyday words beats one
into two rarities). Pure, no Qt; the download is lazy and fail-soft."""

import os
import threading
import urllib.request

# Where downloaded packs live -- gitignored, like the video cache. One
# "<lang>.txt" per language, each line "word count", most-common first
# (the FrequencyWords / OpenSubtitles format).
PACKS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "lexicon_packs")
_SOURCE = ("https://raw.githubusercontent.com/hermitdave/FrequencyWords/"
           "master/content/2018/%s/%s_50k.txt")

MAX_PIECES = 3        # a glued run is two or three words, never a paragraph
MIN_PIECE_LEN = 2     # a one-letter "word" is almost always a bad split
MAX_SPLIT_LEN = 24    # longer tokens aren't glued words worth splitting
_MIN_VOCAB = 1000     # a pack smaller than this is a failed/partial download
SHORT_PIECE_LEN = 3   # a piece this short must be COMMON to be trusted as a
SHORT_PIECE_MAX_RANK = 2000  # split point -- top ~2000, where real short words
                             # live ('you' 1, 'the' 3, 'red' 595) and the
                             # OpenSubtitles pack's noise ('ee' 13679, 'jun'
                             # 7562, 'dd' 8715) does not. Without this the
                             # name 'JUNEEEEDD' tore into 'june eee dd'.

# Languages whose scripts separate words with spaces -- the only ones a
# splitter can help. CJK is excluded by omission (handled by ocr._is_cjk
# too). Only the reduced roster's spaced languages plus "en" (unreachable
# from the picker, but the English pack is the test suite's fixture).
_PACK_LANG = {"en", "id", "ar"}

_lock = threading.Lock()
_packs = {}   # lang -> {word: rank} or None (looked up, absent/failed)


def _pack_path(lang):
    return os.path.normpath(os.path.join(PACKS_DIR, "%s.txt" % lang))


def _read_pack(path):
    """{word: rank} from a 'word count' frequency file, or None if it looks
    unusable. Rank is line order (0 = most common)."""
    ranks = {}
    try:
        with open(path, encoding="utf-8") as f:
            for i, line in enumerate(f):
                word = line.split(" ", 1)[0].strip().casefold()
                if word and word not in ranks:
                    ranks[word] = i
    except OSError:
        return None
    return ranks if len(ranks) >= _MIN_VOCAB else None


def _pack(lang):
    """The loaded pack for `lang`, or None. Read once and cached; a missing
    pack is cached as None so we don't stat the disk every caption."""
    if not lang or lang not in _PACK_LANG:
        return None
    with _lock:
        if lang in _packs:
            return _packs[lang]
        ranks = _read_pack(_pack_path(lang))
        _packs[lang] = ranks
        return ranks


def ensure_pack(lang, timeout=30.0):
    """Download `lang`'s pack if it isn't cached yet. Lazy and fail-soft:
    returns True when a usable pack is present afterwards, False otherwise
    (no network, unsupported language). Safe to call from a worker thread;
    never raises."""
    if not lang or lang not in _PACK_LANG:
        return False
    if _pack(lang) is not None:
        return True
    path = _pack_path(lang)
    try:
        os.makedirs(PACKS_DIR, exist_ok=True)
        req = urllib.request.Request(
            _SOURCE % (lang, lang),
            headers={"User-Agent": "Cappa/0.1 lexicon pack fetch"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except Exception as exc:
        print("[cappa] lexicon: %s pack download failed: %s" % (lang, exc))
        return False
    with _lock:
        _packs.pop(lang, None)   # force a re-read of the new file
    return _pack(lang) is not None


def known(word, lang):
    """Is `word` a real word in `lang`? None when there's no pack to ask
    (so callers can tell 'not a word' from 'don't know')."""
    pack = _pack(lang)
    if pack is None:
        return None
    return word.casefold() in pack


def split(token, lang):
    """`token` broken into real words when it is a run the recogniser glued
    ('CANALWAYS' -> ['CAN', 'ALWAYS']), else [token] unchanged. Splits ONLY
    into pieces that are every one in the pack, so a genuine word is never
    torn apart and an OCR letter-error (no valid segmentation) is left as
    is. Original case/characters are preserved; matching is case-folded.

    No pack, too long, or not all-letters -> [token]. Among valid
    segmentations the one whose words are commonest wins (lowest total
    rank), fewest pieces breaking ties."""
    pack = _pack(lang)
    if pack is None or not token:
        return [token]
    if len(token) > MAX_SPLIT_LEN or not token.isalpha():
        return [token]
    if token.casefold() in pack:
        return [token]           # already a word: never split it

    best = _best_segmentation(token, pack, MAX_PIECES)
    if best is None:
        return [token]           # no all-known split: an OCR error, leave it
    out, i = [], 0
    for length in best:
        out.append(token[i:i + length])
        i += length
    return out


def _bad_piece(piece, rank):
    """A piece the pack technically lists but that is really OCR/subtitle
    noise, not a word to split ON (from the real JUNEEEEDD -> 'june eee dd'
    bug). Two smells: a run of one repeated letter ('ee', 'dd', 'eee') is
    never a word; and a SHORT piece that is RARE is pack junk ('jun' rank
    7562, 'ne' 7215), where a real short word is common ('you' 1, 'red' 595).
    Longer pieces are left to the pack's own judgement."""
    if len(set(piece)) == 1:
        return True
    return len(piece) <= SHORT_PIECE_LEN and rank >= SHORT_PIECE_MAX_RANK


def _best_segmentation(token, pack, max_pieces):
    """Piece LENGTHS of the best all-known split of `token` into 2..max_pieces
    words (each >= MIN_PIECE_LEN, none pack-junk), or None. 'Best' = lowest
    summed rank, then fewest pieces. Small tokens, so a plain recursive search
    is ample."""
    n = len(token)
    best = None   # (total_rank, piece_count, lengths)

    def usable(a, b):
        """The rank of token[a:b] if it's a trustworthy split piece, else None."""
        piece = token[a:b].casefold()
        rank = pack.get(piece)
        if rank is None or _bad_piece(piece, rank):
            return None
        return rank

    def recurse(start, lengths, total):
        nonlocal best
        pieces = len(lengths)
        if start == n:
            if pieces >= 2:
                key = (total, pieces)
                if best is None or key < best[:2]:
                    best = (total, pieces, list(lengths))
            return
        if pieces == max_pieces:
            return
        # leave room for at least one more valid piece
        for end in range(start + MIN_PIECE_LEN, n - MIN_PIECE_LEN + 1):
            rank = usable(start, end)
            if rank is not None:
                lengths.append(end - start)
                recurse(end, lengths, total + rank)
                lengths.pop()
        # the final piece runs to the end
        rank = usable(start, n)
        if rank is not None and n - start >= MIN_PIECE_LEN:
            lengths.append(n - start)
            recurse(n, lengths, total + rank)
            lengths.pop()

    recurse(0, [], 0)
    return best[2] if best else None
