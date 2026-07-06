"""Unit test for cappa/audio.py — the ring-buffer clip math.

No audio device: we push known chunks with known timestamps straight into
the buffer and check that clip()/save_wav()/_trim() cut the right samples.
Live loopback capture is exercised by playing audio and clicking a word."""

import os
import sys
import tempfile
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cappa.audio import LoopbackRecorder

r = LoopbackRecorder()
r.samplerate = 1000   # 1 kHz keeps the test arrays tiny
r.channels = 1
r.ready = True
a = np.arange(0, 1000, dtype=np.int16).reshape(-1, 1)      # spans 100.0-101.0
b = np.arange(1000, 2000, dtype=np.int16).reshape(-1, 1)   # spans 101.0-102.0
r._chunks = [[100.0, a], [101.0, b]]

# a window straddling the chunk boundary, sample-accurate on both edges
got = r.clip(100.5, 101.5)
assert got is not None
data, rate = got
assert rate == 1000
assert data.shape == (1000, 1), data.shape
assert data[0, 0] == 500 and data[-1, 0] == 1499, (data[0, 0], data[-1, 0])
print("PASS: clip straddles two chunks, sample-accurate on both edges")

# fully outside the buffered range -> nothing
assert r.clip(200.0, 201.0) is None
assert r.clip(50.0, 60.0) is None
print("PASS: out-of-window clip returns None")

# not currently recording -> nothing (don't hand back a stale slice)
r.ready = False
assert r.clip(100.5, 101.5) is None
r.ready = True
print("PASS: clip refuses while not ready")

# save_wav writes a real 16-bit PCM WAV of the requested window
p = os.path.join(tempfile.gettempdir(), "cappa_audio_test.wav")
secs = r.save_wav(p, 100.0, 101.0)   # exactly chunk a
assert abs(secs - 1.0) < 1e-6, secs
with wave.open(p, "rb") as w:
    assert w.getframerate() == 1000
    assert w.getnchannels() == 1
    assert w.getsampwidth() == 2
    assert w.getnframes() == 1000
os.remove(p)
assert r.last_clip_peak == 999, r.last_clip_peak   # chunk a peaks at 999
print("PASS: save_wav writes a valid 16-bit PCM WAV of the window")

# save_wav records the clip's peak so the card can flag a silent capture
r._chunks = [[100.0, np.zeros((1000, 1), dtype=np.int16)]]
secs = r.save_wav(p, 100.0, 101.0)
assert secs > 0.0
assert r.last_clip_peak == 0, r.last_clip_peak
os.remove(p)
r._chunks = [[100.0, a], [101.0, b]]
print("PASS: save_wav records the clip peak (0 for a silent capture)")

# _trim drops chunks that fell entirely out of the rolling window
r._buffer_seconds = 1.5
r._chunks = [[100.0, a], [101.0, b]]
r._trim(now=103.0)   # cutoff 101.5; chunk a ends at 101.0 -> dropped
assert len(r._chunks) == 1 and r._chunks[0][0] == 101.0
print("PASS: _trim drops chunks fully older than the window")

# save_wav waits out a window whose tail hasn't played yet: the click-centred
# fallback clip asks for audio past the click, so a chunk that arrives DURING
# the wait must be part of the cut.
import threading
import time

r._buffer_seconds = 90.0
now = time.monotonic()
c = np.arange(0, 500, dtype=np.int16).reshape(-1, 1)     # the 0.5s before now
d = np.arange(500, 800, dtype=np.int16).reshape(-1, 1)   # the 0.3s after now
r._chunks = [[now - 0.5, c]]


def _land_later():
    with r._lock:
        r._chunks.append([now, d])


threading.Timer(0.1, _land_later).start()
secs = r.save_wav(p, now - 0.5, now + 0.3)   # tail is in the future
assert abs(secs - 0.8) < 1e-6, secs
os.remove(p)
print("PASS: save_wav waits for a future tail and includes late chunks")

# The recorder must FOLLOW the default output device: Windows reroutes audio
# (a new default, Bluetooth connect, Steam streaming) without erroring the
# bound endpoint — it just delivers silence forever, and every card's audio
# came out muted (card_0027). Sustained silence must trigger a rebind that
# picks up the current default. Faked pyaudiowpatch: the first PyAudio init
# exposes a dead device (all zeros), every later init a live one.
import types

import cappa.audio as audio_mod


class _FakeStream:
    def __init__(self, value):
        self._value = value

    def read(self, n, exception_on_overflow=False):
        time.sleep(0.005)
        return np.full(n, self._value, dtype=np.int16).tobytes()

    def stop_stream(self):
        pass

    def close(self):
        pass


class _FakePA:
    inits = 0

    def __init__(self):
        _FakePA.inits += 1
        self._name = "Dead Speakers" if _FakePA.inits == 1 else "Live Speakers"

    def get_host_api_info_by_type(self, kind):
        return {"defaultOutputDevice": 7}

    def get_device_info_by_index(self, index):
        return {"name": self._name}

    def get_loopback_device_info_generator(self):
        yield {"name": self._name + " [Loopback]", "index": 3,
               "defaultSampleRate": 1000.0, "maxInputChannels": 1}

    def open(self, **kwargs):
        return _FakeStream(0 if self._name == "Dead Speakers" else 5000)

    def terminate(self):
        pass


fake = types.ModuleType("pyaudiowpatch")
fake.paWASAPI = 13
fake.paInt16 = 8
fake.PyAudio = _FakePA
sys.modules["pyaudiowpatch"] = fake
_old_rebind = audio_mod.SILENT_REBIND
audio_mod.SILENT_REBIND = 0.05
try:
    rec = LoopbackRecorder()
    rec.start()
    deadline = time.time() + 3.0
    heard = False
    while time.time() < deadline and not heard:
        with rec._lock:
            heard = any(int(abs(chunk).max()) > 100
                        for _, chunk in rec._chunks)
        time.sleep(0.02)
    rec.stop()
finally:
    audio_mod.SILENT_REBIND = _old_rebind
    del sys.modules["pyaudiowpatch"]
assert _FakePA.inits >= 2, "never rebound off the silent device"
assert heard, "no audio captured from the device audio moved to"
print("PASS: sustained silence rebinds capture to the current default device")

print("ALL PASS")
