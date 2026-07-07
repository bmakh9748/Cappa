"""The active-video source: one video's caption Transcript plus its audio.

The overlay points this at a video (today from a copied URL via the launcher;
from the browser bridge in the next stage) and the flashcard builder consults
it: given an OCR line's text, `window_for` returns the caption track's exact
[start, end], and `clip_wav` cuts that span from the downloaded audio.

Fetching runs on a daemon thread so the UI never blocks, and is fail-soft in
the same spirit as LoopbackRecorder: any problem (no yt-dlp, no captions, dead
network) just leaves the session not-ready with an `error`/`status`, and card
building falls back to the loopback recorder + OCR timing. The transcript is
published as soon as it parses; the heavier audio download follows, so a card
made in that gap still gets exact *text/timing* provenance and loopback audio.

No Qt here."""

import threading
import time

from . import youtube
from .youtube import SourceError


class SourceSession:
    def __init__(self, lang=None):
        self._lock = threading.Lock()
        self._transcript = None
        self._audio_path = None
        self._video_id = None
        self._url = None
        self._lang = lang
        self._position_provider = None   # () -> browser state dict, or None
        self._mono_mapper = None         # video_t -> monotonic seconds, or None
        self._video_mapper = None        # monotonic seconds -> video_t, or None
        self._audio_retry = threading.Lock()  # one download retry at a time
        self.transcript_ready = False   # captions fetched + aligned-ready
        self.audio_ready = False        # audio downloaded, clips can be cut
        self.error = ""
        self.status = "idle"            # short human string for the tooltip

    # ------------------------------------------------------------ lifecycle
    def set_video(self, url, lang=None):
        """Point the session at a video and fetch its captions (then audio) on
        a daemon thread. A no-op if already on that video."""
        try:
            vid = youtube.extract_video_id(url)
        except SourceError as exc:
            with self._lock:
                self.error, self.status = str(exc), "bad URL"
            return
        with self._lock:
            # Same video again is a no-op only while there's nothing to redo:
            # captions arrived, or a fetch is in flight. A FAILED fetch ("no
            # captions" from a transient bot check / network blip) may be
            # retried by pointing the session at the same video once more.
            if vid == self._video_id and (self.transcript_ready
                                          or self.status == "loading captions"):
                return
            self._video_id = vid
            self._url = youtube.info_url(url)
            if lang is not None:
                self._lang = lang
            self._transcript = None
            self._audio_path = None
            self.transcript_ready = False
            self.audio_ready = False
            self.error = ""
            self.status = "loading captions"
            url_full, lang_now = self._url, self._lang
        threading.Thread(target=self._load, args=(url_full, lang_now, vid),
                         daemon=True).start()

    def _load(self, url, lang, vid):
        try:
            transcript = youtube.fetch_transcript(url, lang=lang)
        except SourceError as exc:
            with self._lock:
                if self._video_id == vid:
                    self.error, self.status = str(exc), "no captions"
            return
        with self._lock:
            if self._video_id != vid:
                return                       # user switched mid-fetch
            self._transcript = transcript
            self.transcript_ready = True
            self.status = "captions ready"

        try:
            path = youtube.fetch_audio(url)
        except SourceError as exc:
            with self._lock:
                if self._video_id == vid:
                    self.error, self.status = str(exc), "captions, no audio"
            return
        with self._lock:
            if self._video_id != vid:
                return
            self._audio_path = path
            self.audio_ready = True
            self.status = "ready"

    def set_position_provider(self, provider):
        """Supply a callable returning the browser's current state dict (from
        the bridge) or None. Enables the position-based, language-neutral
        window."""
        self._position_provider = provider

    def set_language(self, lang):
        """Set the caption-track language preferred for videos fetched from now
        on (None = auto-pick). Does not re-fetch the current video."""
        with self._lock:
            self._lang = lang

    def set_mono_mapper(self, mapper):
        """Supply a callable mapping video seconds -> the monotonic moment that
        audio played through the speakers (the bridge's mono_at). Lets a card
        cut caption-exact audio from the LOOPBACK buffer when the source
        download isn't available."""
        self._mono_mapper = mapper

    def set_video_mapper(self, mapper):
        """Supply a callable mapping a monotonic moment -> the video time then
        playing (the bridge's video_at). Lets a position-matched card anchor
        its clip at the moment the caption APPEARED on screen."""
        self._video_mapper = mapper

    def video_time_at(self, mono):
        """Video seconds playing at monotonic moment `mono`, or None."""
        mapper = self._video_mapper
        if mapper is None or not mono or mono <= 0.0:
            return None
        try:
            return mapper(mono)
        except Exception:
            return None

    # --------------------------------------------------------------- queries
    def window_for(self, ocr_text, near_t=None):
        """The caption track's [start, end] for an OCR line (dict) or None.
        When `near_t` (playback seconds) is given, only captions near it are
        considered -- the fix for a wrong auto-caption matching far away."""
        with self._lock:
            transcript = self._transcript
        if transcript is None:
            return None
        return transcript.window_for(ocr_text, near_t=near_t)

    def play_time(self):
        """The browser's current playback position in seconds, or None."""
        provider = self._position_provider
        if provider is None:
            return None
        try:
            state = provider()
        except Exception:
            return None
        return state.get("play_time") if state else None

    def is_paused(self):
        """Browser-reported paused state, or None when the bridge can't say
        (no extension, stale reports). Three-valued on purpose: only a
        definite 'playing' should make the card path wait on live on-screen
        events (a paused row is frozen — its clear never comes)."""
        provider = self._position_provider
        if provider is None:
            return None
        try:
            state = provider()
        except Exception:
            return None
        if not state:
            return None
        return bool(state.get("paused", False))

    def window_at(self, t):
        """The window of the caption line playing at time `t` (position-based,
        language-neutral), or None."""
        if t is None:
            return None
        with self._lock:
            transcript = self._transcript
        if transcript is None:
            return None
        return transcript.window_at(t)

    def meta(self):
        with self._lock:
            return dict(self._transcript.meta) if self._transcript else {}

    def monotonic_window(self, start, end):
        """Map a caption window (video seconds) to the monotonic window when it
        played through the speakers, or None. The loopback-buffer rescue path:
        exact caption timing without the downloaded audio."""
        mapper = self._mono_mapper
        if mapper is None:
            return None
        try:
            m0, m1 = mapper(start), mapper(end)
        except Exception:
            return None
        if m0 is None or m1 is None or m1 <= m0:
            return None
        return m0, m1

    @property
    def audio_path(self):
        with self._lock:
            return self._audio_path

    def ensure_audio(self, timeout=8.0):
        """The downloaded audio's path, waiting up to `timeout` seconds for an
        in-flight download and retrying once if an earlier attempt failed (bot
        checks and network blips are transient; cookies may have arrived since).
        Returns None if it still isn't available. Blocking -- card threads
        only."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self.audio_ready:
                    return self._audio_path
                in_flight = self.status in ("loading captions", "captions ready")
            if not in_flight:
                break
            time.sleep(0.25)

        with self._audio_retry:
            with self._lock:
                if self.audio_ready:
                    return self._audio_path
                if not self.transcript_ready:
                    return None       # no video / captions failed: nothing to do
                url, vid = self._url, self._video_id
            if not url:
                return None
            try:
                path = youtube.fetch_audio(url)
            except SourceError as exc:
                with self._lock:
                    if self._video_id == vid:
                        self.error, self.status = str(exc), "captions, no audio"
                return None
            with self._lock:
                if self._video_id != vid:
                    return None
                self._audio_path = path
                self.audio_ready = True
                self.status = "ready"
                return path

    def clip_wav(self, out_path, start, end, preroll=0.0, postroll=0.0):
        """Cut [start, end] (+padding) from the downloaded audio into a WAV.
        Raises SourceError if the audio isn't downloaded yet."""
        path = self.audio_path
        if not path:
            raise SourceError("audio not downloaded yet")
        return youtube.slice_audio_wav(path, start, end, out_path,
                                       preroll=preroll, postroll=postroll)
