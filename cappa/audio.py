"""Continuous system-audio recorder — the retroactive clip source for cards.

Audio can't be captured after the fact, so the ONLY way to hand a flashcard
the audio that played while a subtitle was on screen is to record system
audio continuously and slice the clip out afterwards. That's what this is: a
background thread that captures the default output device's WASAPI *loopback*
(what you hear, not a microphone) into a rolling, timestamped ring buffer;
`clip(t0, t1)` / `save_wav(...)` cut any recent window back out.

Clock: every chunk is stamped with time.monotonic() as it arrives — the SAME
clock the capture worker stamps caption appear/clear times with — so a
sentence's timestamps index straight into this buffer (minus the tuned
detection-latency offset the flashcard applies).

Optional and fail-soft: PyAudioWPatch is imported lazily inside the thread.
No library, no loopback device, or a device error just leaves `ready` False
and an explanatory `error` — the app and popup run exactly as before, only
without audio on the card. No Qt here."""

import threading
import time
import wave

CHUNK = 2048           # frames per read (~43 ms at 48 kHz): fine-grained
BUFFER_SECONDS = 90.0  # rolling history kept; a caption clicked older than
                       # this has no audio left. ~17 MB at 48 kHz stereo.


class LoopbackRecorder:
    def __init__(self, buffer_seconds=BUFFER_SECONDS):
        self._buffer_seconds = buffer_seconds
        self._chunks = []   # [[t_start_monotonic, ndarray(frames, ch) int16]]
        self._lock = threading.Lock()
        self._thread = None
        self._running = False
        self.ready = False       # True while a stream is open and buffering
        self.error = ""          # why audio is unavailable, for the UI
        self.samplerate = 0
        self.channels = 0

    # ------------------------------------------------------------ lifecycle
    def start(self):
        """Begin buffering on a daemon thread. Idempotent."""
        if self._thread is not None:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        t = self._thread
        if t is not None:
            t.join(timeout=1.5)
        self._thread = None

    def _run(self):
        try:
            import numpy as np
            import pyaudiowpatch as pyaudio
        except ImportError:
            self.error = "PyAudioWPatch not installed — no audio on cards"
            return

        p = None
        stream = None
        try:
            p = pyaudio.PyAudio()
            wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
            out = p.get_device_info_by_index(wasapi["defaultOutputDevice"])
            loop = None
            for info in p.get_loopback_device_info_generator():
                if out["name"] in info["name"]:
                    loop = info
                    break
            if loop is None:
                self.error = "no loopback device for '%s'" % out["name"]
                return
            self.samplerate = int(loop["defaultSampleRate"])
            self.channels = int(loop["maxInputChannels"]) or 2
            stream = p.open(
                format=pyaudio.paInt16, channels=self.channels,
                rate=self.samplerate, input=True, frames_per_buffer=CHUNK,
                input_device_index=loop["index"],
            )
        except Exception as exc:  # device busy, format unsupported, …
            self.error = "audio open failed: %s" % exc
            self._teardown(stream, p)
            return

        self.ready = True
        try:
            while self._running:
                try:
                    raw = stream.read(CHUNK, exception_on_overflow=False)
                except Exception as exc:  # device unplugged / switched
                    self.error = "audio read stopped: %s" % exc
                    break
                t_end = time.monotonic()
                arr = np.frombuffer(raw, dtype=np.int16)
                arr = arr.reshape(-1, self.channels)
                t_start = t_end - arr.shape[0] / self.samplerate
                with self._lock:
                    self._chunks.append([t_start, arr])
                    self._trim(t_end)
        finally:
            self.ready = False
            self._teardown(stream, p)

    @staticmethod
    def _teardown(stream, p):
        if stream is not None:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
        if p is not None:
            try:
                p.terminate()
            except Exception:
                pass

    def _trim(self, now):
        """Drop chunks that fell entirely out of the rolling window. Caller
        holds the lock."""
        cutoff = now - self._buffer_seconds
        rate = self.samplerate or 1
        i = 0
        chunks = self._chunks
        while i < len(chunks):
            t_start, arr = chunks[i]
            if t_start + arr.shape[0] / rate >= cutoff:
                break
            i += 1
        if i:
            del chunks[:i]

    # --------------------------------------------------------------- output
    def clip(self, t0, t1):
        """(int16 ndarray (frames, ch), samplerate) for monotonic window
        [t0, t1], or None if unavailable / out of the buffer. Edges are
        trimmed to the exact window using each chunk's own start time."""
        import numpy as np
        if not self.ready or self.samplerate == 0 or t1 <= t0:
            return None
        rate = self.samplerate
        with self._lock:
            snapshot = list(self._chunks)  # copy the refs, not the arrays
        parts = []
        for t_start, arr in snapshot:
            t_stop = t_start + arr.shape[0] / rate
            if t_stop <= t0 or t_start >= t1:
                continue
            start_i = max(0, int(round((t0 - t_start) * rate)))
            stop_i = min(arr.shape[0], int(round((t1 - t_start) * rate)))
            if stop_i > start_i:
                parts.append(arr[start_i:stop_i])
        if not parts:
            return None
        return np.concatenate(parts, axis=0), rate

    def save_wav(self, path, t0, t1):
        """Write the [t0, t1] window to a 16-bit PCM WAV. Returns the clip's
        duration in seconds, or 0.0 if nothing was available."""
        got = self.clip(t0, t1)
        if got is None:
            return 0.0
        data, rate = got
        with wave.open(path, "wb") as w:
            w.setnchannels(data.shape[1])
            w.setsampwidth(2)   # int16
            w.setframerate(rate)
            w.writeframes(data.tobytes())
        return data.shape[0] / rate
