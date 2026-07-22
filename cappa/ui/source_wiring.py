"""The video-source machinery behind the overlay, in one place.

Everything the overlay needs to KNOW about the video being watched — which
YouTube video is up (the browser bridge), its caption track and audio (the
source session), what the speakers are playing (the loopback recorder), and
Cappa's own transcript of the captions it saw (the OCR log) — is constructed,
gated and glued here. The overlay stays a painter and hit-tester: its tick
calls poll(), detection results pass through observe_captions(), and the
launcher tooltip/dot read status_suffix()/yt_light(); the word popup borrows
.recorder and .session for card building.

Qt-free on purpose: progress messages surface through the on_tip callback,
so a future reading mode without a tracked screen region (the page-text
plan) can reuse this wiring without the overlay."""

import time

from ..audio import LoopbackRecorder
from ..flashcard import prefs as card_prefs
from ..source.bridge import BrowserBridge
from ..source.ocr_transcript import OcrTranscriptLog
from ..source.session import SourceSession

RECORDER_LINGER = 5.0   # bridge silent this long -> pause the recorder
                        # (~10s after leaving the tab: visible-tab-only
                        # reports + the bridge's 5s hold)
DOT_STALE = 2.0      # ~3 missed ~700ms extension ticks -> yt dot goes dark;
                     # the recorder keeps RECORDER_LINGER — stop/start churn
                     # is costly, a dark dot isn't
CAPTION_RETRY_WAIT = 20.0  # cooldown between caption-fetch retries
CAPTION_RETRY_MAX = 2      # retries per video: transient failures (a bot
                           # check before the extension's cookies landed, a
                           # network blip) heal; a genuinely captionless
                           # video stops costing fetches after two tries
VIDEO_ID_DEBOUNCE = 1.5    # only an id STABLE this long earns a session
                           # fetch (else one download per Short scrolled
                           # past); attribution still follows the reported
                           # id instantly


def live_video_id(state, bridge_video_id):
    """The video id on-screen captions may be attributed to RIGHT NOW, or
    None. Only a fresh, playing bridge report counts. `state` is
    bridge.current() (None once the extension's reports go stale — the tab
    was hidden, closed, or isn't YouTube at all); a paused report is a
    frozen frame, not caption life. In either case whatever the overlay is
    reading is NOT this video's caption and must not land in its transcript
    under the last-known id."""
    if state is None or state.get("paused"):
        return None
    return bridge_video_id


class SourceWiring:
    def __init__(self, video_language=None, on_tip=None):
        self._on_tip = on_tip or (lambda text: None)
        # Rolling system-audio buffer so a clicked word's clip is already
        # captured at click time. Privacy-gated by _gate_recorder (see its
        # docstring); extension-less sessions record continuously.
        # Fail-soft — no device just means no audio.
        self.recorder = LoopbackRecorder()
        # Card audio OFF in settings stands the whole video machinery down:
        # no recording, no auto-select, no downloads. poll() re-reads the
        # setting each tick, so the panel retunes it live.
        self._audio_off = not card_prefs.include("audio")
        if self._audio_off:
            self.recorder.error = ("recording off — card audio is "
                                   "disabled in settings")
        else:
            self.recorder.start()
        self._bridge_ever = False     # extension reported at least once
        self._bridge_lost_at = None   # when reports stopped (for the linger)
        self._recorder_paused = False # paused by the gate (not by errors)
        # The active YouTube video: when it can align a caption line, cards
        # get exact caption-track audio instead of the loopback buffer.
        # Fail-soft — stays idle otherwise. The browser bridge points it at
        # whatever YouTube tab is playing and feeds it the live playback
        # position; the launcher's clipboard action is the manual fallback.
        self.session = SourceSession(lang=video_language)
        self.bridge = BrowserBridge()
        self.bridge.start()
        self.session.set_position_provider(self.bridge.current)
        # Lets a card cut caption-exact audio from the loopback buffer when
        # the source download is missing (bot check, still in flight, ...).
        self.session.set_mono_mapper(self.bridge.mono_at)
        # ...and lets a position-matched card anchor its clip at the moment
        # the caption appeared on screen (mono -> video time).
        self.session.set_video_mapper(self.bridge.video_at)
        # ...and refuse that anchor when the row appeared because of a
        # pause/seek rather than during continuous playback (user rule:
        # a row must be SEEN appearing for its stamp to mean anything).
        self.session.set_steady_prober(self.bridge.steady_at)
        # ...and when the live anchor is refused, let the card recall where
        # an EARLIER steady watch saw this exact row pop (the OCR transcript
        # log below writes those down — card_0009's watch-rewind-click).
        self.session.set_sighting_lookup(
            lambda text, near_t=None: self.ocr_log.window_hint(
                self._bridge_video_id, text, near_t))
        # ...and how fast this video's speaker talks, so a line still being
        # spoken at click time has its end PREDICTED from the words still to
        # come rather than cut at a flat tail.
        self.session.set_rate_lookup(
            lambda: self.ocr_log.seconds_per_word(self._bridge_video_id))
        # ...and this run's watched rows in a video-time window, so a card's
        # sentence can be completed from what WE saw plus what the track
        # heard (our transcript first, the track filling the holes).
        self.session.set_rows_lookup(
            lambda t0, t1: self.ocr_log.rows_between(
                self._bridge_video_id, t0, t1))
        self._bridge_video_id = None      # the video the browser shows NOW
        self._session_video_id = None     # the video the session fetched for
        self._vid_since = 0.0             # when _bridge_video_id last changed
        self._restarts_seen = 0           # consumed bridge.restart_count
        self._caption_retry = (None, 0, 0.0)  # (video, attempts, last try)
        # Cappa's own transcript of the captions it watches: rows that leave
        # the screen are appended to transcripts/<video>.jsonl with their
        # on-screen life mapped to video time.
        self.ocr_log = OcrTranscriptLog()
        self._ext_version = ""        # extension version, as reported by it
        self._status_shown = ""       # last session status poll() reported on
        self._ready_tipped = False    # 'captions ready' announced this video
        if self.bridge.error:
            print("[cappa] browser bridge: " + self.bridge.error)
        # The media cache (downloaded audio/captions) is a convenience, not
        # a record: prune it to its cap. cards/ and transcripts/ are the
        # app's memory and are never pruned.
        try:
            from ..source.youtube import prune_cache
            n = prune_cache()
            if n:
                print("[cappa] media cache: pruned %d old file(s)" % n)
        except Exception:
            pass

    # ------------------------------------------------------------------ tick
    def poll(self):
        """Follow whatever YouTube video the browser reports and reflect the
        background caption-fetch progress. Returns True when the tooltip-
        visible status changed, so the caller re-renders (even while idle,
        when no fps events are firing)."""
        self._poll_browser()
        if self.session.status != self._status_shown:
            self._status_shown = self.session.status
            self._announce()
            return True
        return False

    def _poll_browser(self):
        """Auto-select the video the browser extension reports, and gate the
        audio recorder on those reports arriving at all (_gate_recorder). A
        no-op when the extension isn't installed / the bridge is down
        (current() -> None) or the video hasn't changed, so the manual
        clipboard path still works. All of it stands down while card audio
        is off in the settings — nothing would use what it gathers."""
        off = not card_prefs.include("audio")
        if off != self._audio_off:
            self._audio_off = off
            if off:
                self._bridge_video_id = None   # re-select on re-enable
                self._session_video_id = None
                self._bridge_lost_at = None
                self.recorder.stop()
                self.recorder.error = ("recording off — card audio is "
                                       "disabled in settings")
            elif not self._recorder_paused:
                self.recorder.start()
        if self._audio_off:
            return
        state = self.bridge.current()
        self._gate_recorder(alive=state is not None)
        if not state:
            return
        self._ext_version = state.get("ext") or self._ext_version
        vid = state.get("videoId")
        if vid and vid != self._bridge_video_id:
            # Attribution follows the screen instantly; the FETCH waits for
            # the id to hold still (VIDEO_ID_DEBOUNCE) so scrolling the
            # Shorts feed doesn't fire a download per short flicked past.
            self._bridge_video_id = vid
            self._vid_since = time.monotonic()
        if vid and vid != self._session_video_id:
            if time.monotonic() - self._vid_since >= VIDEO_ID_DEBOUNCE:
                self._session_video_id = vid
                self.session.set_video(state.get("url") or vid)
                print("[cappa] source: browser video %s (%s)"
                      % (vid, (state.get("title") or "?")[:50]))
        elif (vid and not self.session.fetching
                and self.session.status.startswith("no captions")):
            # Covers "no captions" and "no captions, audio ready": captions
            # may appear on retry (bot check before the cookies arrived); the
            # already-downloaded audio just re-resolves from the cache.
            self._retry_captions(vid, state)

    def _retry_captions(self, vid, state):
        """Retry a transiently failed caption fetch (bot check before the
        extension's cookies landed, a network blip) a couple of times with
        a cooldown."""
        r_vid, tries, at = self._caption_retry
        if r_vid != vid:
            r_vid, tries, at = vid, 0, 0.0
        now = time.monotonic()
        if tries >= CAPTION_RETRY_MAX or now - at < CAPTION_RETRY_WAIT:
            self._caption_retry = (r_vid, tries, at)
            return
        self._caption_retry = (vid, tries + 1, now)
        print("[cappa] source: retrying captions for %s (%d/%d)"
              % (vid, tries + 1, CAPTION_RETRY_MAX))
        self.session.set_video(state.get("url") or vid)

    def _gate_recorder(self, alive):
        """Record system audio only while the extension is reporting a
        visible YouTube tab. `alive` is this tick's bridge freshness. Only
        ever arms itself once the extension has spoken (_bridge_ever), so a
        session without the extension records continuously as before; after
        that, reports stopping for RECORDER_LINGER means the user left the
        tab — stop capturing until they're back. Cards built while paused
        note the recorder's `error` instead of getting someone-else's-audio
        clips."""
        if alive:
            self._bridge_ever = True
            self._bridge_lost_at = None
            if self._recorder_paused:
                self._recorder_paused = False
                self.recorder.start()
            return
        if not self._bridge_ever or self._recorder_paused:
            return
        now = time.monotonic()
        if self._bridge_lost_at is None:
            self._bridge_lost_at = now
        elif now - self._bridge_lost_at >= RECORDER_LINGER:
            self._recorder_paused = True
            self.recorder.stop()
            self.recorder.error = "recording paused — no YouTube tab in sight"

    def _announce(self):
        """The launcher tooltip only shows on hover, so caption-track progress
        was invisible — tip the transitions that matter: the track becoming
        usable, this video not having one, or a caption-less video's AUDIO
        landing (cards are fine without captions — the screen times the
        clips; say so instead of looking broken)."""
        if self.session.transcript_ready:
            if not self._ready_tipped:
                self._ready_tipped = True
                self._on_tip(
                    "YouTube captions ready — cards get caption-exact audio")
        elif self.session.status == "no captions, audio ready":
            if not self._ready_tipped:
                self._ready_tipped = True
                self._on_tip("No captions on this video — audio ready, "
                             "clips timed from the screen")
        else:
            self._ready_tipped = False
            if self.session.status in ("no captions, no audio", "bad URL"):
                self._on_tip("YouTube audio unavailable (%s) — card "
                             "audio falls back to what just played"
                             % self.session.status)

    # -------------------------------------------------------------- captions
    def observe_captions(self, captions):
        """Cappa's own transcript: rows that just left the screen get written
        down with their on-screen life — the durable record cards cite. Only
        while a YouTube video is genuinely live in front of us: a stale
        report (tab hidden/closed/not YouTube) or a paused one yields no
        video id, and observe() logs nothing without one. The bridge's pass
        counter rides along so a looping Short's every pass is logged (the
        REPEAT_GAP dedupe only swallows repeats within one pass)."""
        vid = live_video_id(self.bridge.current(), self._bridge_video_id)
        self.ocr_log.observe(vid, captions, self.bridge.video_at,
                             self.bridge.pass_count())

    def video_restarted(self):
        """True exactly once per bridge-observed RESTART (a looping Short
        wrapping to the top, or a seek to 0). The overlay's tick answers by
        force-clearing detection: the screen belongs to a new pass, and a
        caption identical across the wrap must be re-accepted with a fresh
        appear stamp instead of keeping the previous pass's (which would
        time its clip against the wrong playthrough)."""
        n = self.bridge.restart_count
        if n != self._restarts_seen:
            self._restarts_seen = n
            return True
        return False

    # ---------------------------------------------------------------- status
    def status_suffix(self):
        """The source tail of the launcher tooltip: card audio off / yt
        status / a dead bridge / the extension version."""
        text = ""
        if self._audio_off:
            text += "   ·   card audio off"
        elif self.session.status != "idle":
            text += "   ·   yt: " + self.session.status
        elif self.bridge.error:
            # A dead bridge otherwise looks exactly like "no YouTube open"
            # (the port collision with AnkiConnect hid behind that for days).
            text += "   ·   ⚠ yt bridge down: port in use?"
        if self._ext_version:
            text += "   ·   ext " + self._ext_version
        return text

    def yt_light(self):
        """The launcher's video-source dot: 'ready' (green) = the next card
        gets good audio — the caption track, or (caption-less) the downloaded
        source audio with clips timed from the screen (user call, cards
        0001/0002: no captions is a NOTE, not a failure); 'loading' (amber) =
        fetch in flight, 'error' (red) = neither captions nor audio, None
        (dark) = no video yet — or nothing is WATCHING one: the YouTube tab
        closed (the recorder gate's signal) or card audio is off."""
        if self._audio_off or self._recorder_paused:
            return None
        age = self.bridge.age()
        if age is not None and age > DOT_STALE:
            return None   # tab just closed/hidden: dark within ~2s
        if self.session.transcript_ready or self.session.audio_ready:
            return "ready"
        if self.session.fetching:
            return "loading"
        status = self.session.status
        if status == "idle":
            return None
        if status == "loading captions":
            return "loading"
        return "error"

    # ------------------------------------------------------------- lifecycle
    def set_language(self, lang):
        """Caption tracks fetched from now on prefer this language (the
        current video keeps its captions)."""
        self.session.set_language(lang)

    def stop(self):
        self.recorder.stop()
        self.bridge.stop()
