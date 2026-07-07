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

RECORDER_LINGER = 5.0   # bridge silent this long -> pause the audio recorder.
                        # The extension posts only from a VISIBLE YouTube tab
                        # (content.js skips document.hidden) and the bridge
                        # already holds reports for 5s, so the recorder stops
                        # ~10s after the user leaves the tab.
DOT_STALE = 2.0      # extension silent this long -> the yt dot goes dark.
                     # The content script posts ~700ms ticks from a VISIBLE
                     # tab, so three missed ticks means the tab was closed
                     # or hidden. The dot reacts here, near-instantly; the
                     # RECORDER keeps its longer linger above — stop/start
                     # churn on every tab flick is costly, a dark dot isn't.
CAPTION_RETRY_WAIT = 20.0  # cooldown between caption-fetch retries
CAPTION_RETRY_MAX = 2      # retries per video: transient failures (a bot
                           # check before the extension's cookies landed, a
                           # network blip) heal; a genuinely captionless
                           # video stops costing fetches after two tries


class SourceWiring:
    def __init__(self, video_language=None, on_tip=None):
        self._on_tip = on_tip or (lambda text: None)
        # System-audio recorder: rolling buffer so a clicked word's clip is
        # already captured when they click. NOT unconditional (user call:
        # recording their system audio while they're off the YouTube tab is
        # not okay): once the extension has reported a visible YouTube tab,
        # _gate_recorder pauses capture whenever those reports stop, and
        # resumes when they're back. Sessions where the extension never
        # speaks keep the old always-on recorder — it's the only audio their
        # cards can get. Fail-soft — no device just means no audio.
        self.recorder = LoopbackRecorder()
        # Card audio OFF in settings leaves the whole video machinery
        # without a customer: don't record, don't auto-select videos, no
        # caption/audio downloads (the user's call: "if i'm not using audio
        # i really don't need you to track the video"). poll() re-reads the
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
        self._bridge_video_id = None      # last video the bridge auto-selected
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
            self._bridge_video_id = vid
            self.session.set_video(state.get("url") or vid)
            print("[cappa] source: browser video %s (%s)"
                  % (vid, (state.get("title") or "?")[:50]))
        elif vid and self.session.status == "no captions":
            self._retry_captions(vid, state)

    def _retry_captions(self, vid, state):
        """A caption fetch that failed can be transient (bot check before the
        extension's cookies arrived, a network blip) — but it used to be
        PERMANENT: the session refused the same video and the poll never
        re-selected it, so the dot sat red for the whole watch unless the
        user changed videos (which is why it kept coming back on tab
        switches). Retry a couple of times with a cooldown instead."""
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
        usable, or this video not having one."""
        if self.session.transcript_ready:
            if not self._ready_tipped:
                self._ready_tipped = True
                self._on_tip(
                    "YouTube captions ready — cards get caption-exact audio")
        else:
            self._ready_tipped = False
            if self.session.status in ("no captions", "bad URL"):
                self._on_tip("YouTube captions unavailable (%s) — card "
                             "audio falls back to what just played"
                             % self.session.status)

    # -------------------------------------------------------------- captions
    def observe_captions(self, captions):
        """Cappa's own transcript: rows that just left the screen get written
        down with their on-screen life — the durable record cards cite."""
        self.ocr_log.observe(self._bridge_video_id, captions,
                             self.bridge.video_at)

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
        """The launcher's caption-source dot: 'ready' (green) = caption track
        usable for cards, 'loading' (amber) = fetch in flight, 'error' (red)
        = this video has no usable track, None (dark) = no video yet — or
        nothing is WATCHING one: the YouTube tab closed (the recorder gate's
        signal) or card audio is off. A green dot must mean "the next card
        gets caption-exact audio", which is a lie once the tab is gone."""
        if self._audio_off or self._recorder_paused:
            return None
        age = self.bridge.age()
        if age is not None and age > DOT_STALE:
            return None   # tab just closed/hidden: dark within ~2s
        if self.session.transcript_ready:
            return "ready"
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
