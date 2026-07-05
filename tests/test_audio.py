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
print("PASS: save_wav writes a valid 16-bit PCM WAV of the window")

# _trim drops chunks that fell entirely out of the rolling window
r._buffer_seconds = 1.5
r._chunks = [[100.0, a], [101.0, b]]
r._trim(now=103.0)   # cutoff 101.5; chunk a ends at 101.0 -> dropped
assert len(r._chunks) == 1 and r._chunks[0][0] == 101.0
print("PASS: _trim drops chunks fully older than the window")

print("ALL PASS")
