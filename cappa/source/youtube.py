"""yt-dlp / ffmpeg backend: fetch a video's metadata, caption track and audio.

Everything network- or binary-dependent lives here so the rest of cappa.source
stays pure. yt-dlp is imported lazily (like PyAudioWPatch in audio.py), so
importing the package never needs the network or the library; a missing yt-dlp,
no captions, or a dead network raise SourceError for the caller to note on the
card instead of crashing the overlay.

Fetched captions and audio cache per videoId under source/.cache, so a video is
pulled once and every later card on it is instant.

Operational caveat, since materialized: YouTube format extraction now REQUIRES
a JavaScript runtime plus yt-dlp's challenge solver scripts (yt-dlp-ejs, from
pip install "yt-dlp[default]"). _ydl_opts enables both deno and node; the
machine must have one of them installed or audio downloads fail with
'Requested format is not available' while captions keep working."""

import os
import subprocess
import urllib.request

from .transcript import Transcript
from .vtt import parse_vtt

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")
_UA = "Mozilla/5.0"        # some subtitle CDNs 403 an empty user agent


class SourceError(Exception):
    """A video's captions or audio could not be fetched."""


# --------------------------------------------------------------- video ids
def extract_video_id(url_or_id):
    """The 11-char YouTube id from a watch/share/embed URL, or the input if it
    already looks like a bare id."""
    import re
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url_or_id or ""):
        return url_or_id
    m = re.search(r"(?:v=|/embed/|/shorts/|youtu\.be/)([A-Za-z0-9_-]{11})",
                  url_or_id or "")
    if not m:
        raise SourceError("no video id in %r" % url_or_id)
    return m.group(1)


# --------------------------------------------------------------- cookies
def cookie_file_path(cache_dir=CACHE_DIR):
    """Where the browser extension's YouTube cookies land (via the bridge).
    YouTube bot-checks anonymous fetchers ('Sign in to confirm you're not a
    bot'); logged-in cookies pass it. yt-dlp's own --cookies-from-browser can't
    decrypt current Chrome/Edge cookies on Windows (App-Bound Encryption), but
    our extension reads them natively and POSTs them to the bridge, which
    writes this Netscape-format file."""
    return os.path.join(cache_dir, "cookies.txt")


def _ydl_opts(**extra):
    """Base yt-dlp options; attaches the extension-supplied cookie file when
    one exists so fetches ride the user's logged-in session.

    js_runtimes: YouTube format URLs now carry a JS challenge yt-dlp must run
    a real JavaScript engine to solve; without one every video download dies
    with 'Requested format is not available' (captions survive: they come
    from metadata). yt-dlp only enables deno by default — enable node too so
    whichever the machine has gets used (card_0047's machine has node)."""
    opts = {"quiet": True, "no_warnings": True,
            "js_runtimes": {"deno": {}, "node": {}}}
    cookies = cookie_file_path()
    if os.path.exists(cookies):
        opts["cookiefile"] = cookies
    opts.update(extra)
    return opts


def _friendly(exc):
    """Rewrite yt-dlp's scariest failures into short, actionable reasons."""
    text = str(exc)
    if "Sign in to confirm" in text or "not a bot" in text:
        if os.path.exists(cookie_file_path()):
            return "YouTube bot check (cookies may be stale -- reload the "\
                   "Cappa Bridge extension)"
        return "YouTube bot check -- install the Cappa Bridge extension so "\
               "your logged-in cookies can be used"
    if "Requested format is not available" in text or "No video formats" in text:
        return "YouTube offered no playable formats -- yt-dlp needs a JS "\
               "runtime (install deno or Node.js) and its challenge solver "\
               "(pip install -U \"yt-dlp[default]\")"
    return text


# --------------------------------------------------------------- fetching
def fetch_info(url):
    """yt-dlp's metadata dict for a video (no download). Lazy-imports yt-dlp.

    ignore_no_formats_error is essential: extract_info runs video-format
    selection even for a metadata-only fetch, and a video yt-dlp can't get
    playable formats for (bot check, extraction breakage, no JS runtime)
    raises 'Requested format is not available' — which would wrongly kill
    the CAPTION fetch, though the subtitle lists are right there in the
    metadata and don't need formats at all (card_0038)."""
    try:
        import yt_dlp
    except ImportError as exc:
        raise SourceError("yt-dlp not installed") from exc
    opts = _ydl_opts(skip_download=True, ignore_no_formats_error=True)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as exc:  # network, geo-block, private, bot check, ...
        raise SourceError("info fetch failed: %s" % _friendly(exc)) from exc


def fetch_transcript(url, lang=None, prefer_manual=True, cache_dir=CACHE_DIR):
    """Fetch and parse a video's caption track into a Transcript.

    Prefers an uploader (manual) track in `lang`, else an auto-generated one.
    `lang` None picks the video's own language. Raises SourceError if no usable
    track exists."""
    info = fetch_info(url)
    vid = info.get("id") or extract_video_id(url)
    chosen_lang, is_auto, sub_url = _pick_subtitle(info, lang, prefer_manual)

    os.makedirs(cache_dir, exist_ok=True)
    kind = "auto" if is_auto else "man"
    cache = os.path.join(cache_dir, "%s.%s.%s.vtt" % (vid, chosen_lang, kind))
    if os.path.exists(cache):
        content = open(cache, encoding="utf-8").read()
    else:
        content = _download_text(sub_url)
        with open(cache, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)

    tokens = parse_vtt(content)
    if not tokens:
        raise SourceError("caption track parsed to nothing (%s/%s)"
                          % (chosen_lang, kind))
    meta = {
        "video_id": vid,
        "url": info.get("webpage_url") or url,
        "title": info.get("title") or "",
        "channel": info.get("channel") or info.get("uploader") or "",
        "duration": info.get("duration") or 0,
        "thumbnail": info.get("thumbnail") or "",
        "caption_lang": chosen_lang,
        "caption_auto": is_auto,
    }
    return Transcript(tokens, meta)


def _pick_subtitle(info, lang, prefer_manual):
    """(lang_code, is_auto, vtt_url) for the best available track.

    LANGUAGE first, source second. A track in the requested (or the
    video's own) language is text-matchable against the OCR lines even
    when machine-made; a human track in ANOTHER language is a translation
    that can never match — picking it forces every card onto coarse
    position timing (cards 0069/0071: the manual ENGLISH track was chosen
    over the video's own Indonesian auto captions). Within one language,
    `prefer_manual` decides between the uploader's track and the auto
    captions.

    Auto captions come with machine-TRANSLATED variants for every language
    on Earth; only the spoken-language track (the `xx-orig` variant, or
    the code matching the video's language) is usable, so the last resorts
    stay narrow: any -orig auto track, then any manual track (real timing
    in a real language, position-only) -- and never an arbitrary auto
    translation (dict order once handed us Abkhazian)."""
    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    order = []
    for code in (lang, info.get("language")):
        if code and code not in order:
            order.append(code)
            order.append(code + "-orig")

    pools = [(manual, False), (auto, True)]
    if not prefer_manual:
        pools.reverse()

    for code in order:
        for pool, is_auto in pools:
            if code in pool:
                url = _vtt_url(pool[code])
                if url:
                    return code, is_auto, url
    # Last resort in the auto pool: the spoken-language original.
    for code, fmts in auto.items():
        if code.endswith("-orig"):
            url = _vtt_url(fmts)
            if url:
                return code, True, url
    # Any uploader track: real timing in a real language (position mode).
    for code, fmts in manual.items():
        url = _vtt_url(fmts)
        if url:
            return code, False, url
    raise SourceError("no usable subtitles or auto-captions")


def _vtt_url(formats):
    for fmt in formats or []:
        if fmt.get("ext") == "vtt" and fmt.get("url"):
            return fmt["url"]
    return None


def _download_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read().decode("utf-8", "replace")
    except Exception as exc:
        raise SourceError("subtitle download failed: %s" % exc) from exc


# --------------------------------------------------------------- audio
def fetch_audio(url, cache_dir=CACHE_DIR):
    """Download the video's best audio track once; return the local file path.
    Cached per videoId. Raises SourceError on failure."""
    try:
        import yt_dlp
    except ImportError as exc:
        raise SourceError("yt-dlp not installed") from exc
    vid = extract_video_id(info_url(url))
    os.makedirs(cache_dir, exist_ok=True)
    existing = _cached_audio(cache_dir, vid)
    if existing:
        return existing
    opts = _ydl_opts(
        skip_download=False,
        format="bestaudio/best",
        outtmpl=os.path.join(cache_dir, "%(id)s.%(ext)s"),
    )
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except Exception as exc:
        raise SourceError("audio download failed: %s" % _friendly(exc)) from exc
    got = _cached_audio(cache_dir, vid)
    if not got:
        raise SourceError("audio download produced no file")
    return got


def info_url(url):
    """Normalize a bare id or URL to a full watch URL for yt-dlp."""
    import re
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url or ""):
        return "https://www.youtube.com/watch?v=%s" % url
    return url


def _cached_audio(cache_dir, vid):
    for name in os.listdir(cache_dir) if os.path.isdir(cache_dir) else []:
        base, ext = os.path.splitext(name)
        if base == vid and ext.lower() in (".m4a", ".webm", ".opus", ".mp3",
                                           ".mp4", ".ogg"):
            return os.path.join(cache_dir, name)
    return None


def slice_audio_wav(audio_path, start, end, out_path, preroll=0.0, postroll=0.0):
    """Cut [start-preroll, end+postroll] from a downloaded audio file into a
    16-bit PCM WAV with ffmpeg. Sample-accurate (output seek). Returns the clip
    duration in seconds. Raises SourceError if ffmpeg is missing or fails."""
    import shutil
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SourceError("ffmpeg not on PATH")
    t0 = max(0.0, start - preroll)
    t1 = max(t0 + 0.05, end + postroll)
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", audio_path, "-ss", "%.3f" % t0, "-to", "%.3f" % t1,
        "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", out_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except Exception as exc:
        raise SourceError("ffmpeg slice failed: %s" % exc) from exc
    return t1 - t0
