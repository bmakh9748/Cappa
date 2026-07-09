"""A rolling screen recording of the tracked area, for debugging bad cards.

When a card comes out wrong, the transcript says what Cappa READ but not what
was actually on screen at that instant. This records the tracked region to
short MP4 segments so a bad moment can be scrubbed back to and watched. A
developer aid, not part of the product, and built to be unable to hurt the
app: entirely fail-soft (no ffmpeg, no region, a dead encoder -> it simply
doesn't record) and self-capped, so it can never fill the disk -- recordings/
is pruned oldest-first to MAX_BYTES (5 GB) on every segment rotation.

Its own capture thread and its own mss grabber (mss binds to the thread that
made it), independent of the detection worker, so recording never slows the
neural loop. Frames pipe as raw BGR into ffmpeg encoding H.264; a segment
rotates on a timer OR whenever the region's size changes -- a resized area
starts a fresh clip rather than corrupting the stream. No Qt."""

import os
import shutil
import subprocess
import threading
import time
from datetime import datetime

from .detection.capture import ScreenCapture

RECORDINGS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "recordings")
MAX_BYTES = 5 * 1024 * 1024 * 1024   # 5 GB hard cap; oldest segments pruned
FPS = 8                  # enough to see caption timing; tiny on disk
SEGMENT_S = 30.0         # one file per ~30s, so pruning drops whole clips
# Windows: keep ffmpeg's console from flashing; 0 (a no-op flag) elsewhere.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def prune_recordings(out_dir=RECORDINGS_DIR, max_bytes=MAX_BYTES):
    """Delete the oldest recording segments until recordings/ fits max_bytes.
    Returns how many files were removed. Fail-soft: a missing folder or a file
    in use (the segment currently being written) is simply skipped."""
    try:
        names = os.listdir(out_dir)
    except OSError:
        return 0
    files = []
    for name in names:
        path = os.path.join(out_dir, name)
        try:
            st = os.stat(path)
        except OSError:
            continue
        if os.path.isfile(path):
            files.append((st.st_mtime, st.st_size, path))
    total = sum(size for _, size, _ in files)
    removed = 0
    for _, size, path in sorted(files):        # oldest first
        if total <= max_bytes:
            break
        try:
            os.remove(path)
        except OSError:
            continue
        total -= size
        removed += 1
    return removed


class RegionRecorder:
    """Records region_provider()'s rectangle to recordings/area_<time>.mp4
    segments. region_provider() returns (left, top, width, height) physical
    pixels, or None when there's nothing to record (idle/parked) -- the same
    callback the capture worker uses, so the footage matches what was detected.
    start()/stop() are safe to call when ffmpeg is absent."""

    def __init__(self, region_provider, out_dir=RECORDINGS_DIR, fps=FPS,
                 max_bytes=MAX_BYTES, segment_s=SEGMENT_S):
        self._region_provider = region_provider
        self._out_dir = out_dir
        self._fps = fps
        self._max_bytes = max_bytes
        self._segment_s = segment_s
        self._thread = None
        self._running = False
        self.error = ""

    def start(self):
        """Begin recording on a daemon thread. A no-op if already running or
        if ffmpeg isn't installed (the whole feature just stays off)."""
        if self._thread is not None:
            return
        if not shutil.which("ffmpeg"):
            self.error = "ffmpeg not on PATH — area recording off"
            print("[cappa] recorder: " + self.error)
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        print("[cappa] recorder: area recording to %s (<= %d GB)"
              % (os.path.normpath(self._out_dir),
                 self._max_bytes // (1024 ** 3)))

    def stop(self):
        self._running = False
        t = self._thread
        if t is not None:
            t.join(timeout=3.0)
        self._thread = None

    # ------------------------------------------------------------- internals
    def _run(self):
        try:
            capture = ScreenCapture()
        except Exception as exc:
            self.error = "screen capture unavailable: %s" % exc
            return
        try:
            os.makedirs(self._out_dir, exist_ok=True)
        except OSError:
            pass
        proc = None
        seg_size = None       # (w, h) the open segment was started at
        seg_started = 0.0
        interval = 1.0 / self._fps
        try:
            while self._running:
                loop = time.perf_counter()
                region = self._region()
                if region is None:
                    proc = self._close(proc)     # idle: finalize the segment
                    time.sleep(0.25)
                    continue
                try:
                    img = capture.grab(region)
                except Exception:
                    time.sleep(0.1)
                    continue
                h, w = img.shape[:2]
                now = time.monotonic()
                if (proc is None or (w, h) != seg_size
                        or now - seg_started >= self._segment_s):
                    # Rotate: a new file every SEGMENT_S (so a prune can drop
                    # whole clips) or the moment the region resizes (a rawvideo
                    # stream can't change dimensions mid-file).
                    proc = self._close(proc)
                    prune_recordings(self._out_dir, self._max_bytes)
                    proc = self._open(w, h)
                    seg_size, seg_started = (w, h), now
                    if proc is None:
                        time.sleep(0.5)          # ffmpeg wouldn't start
                        continue
                try:
                    proc.stdin.write(img[:, :, :3].tobytes())  # BGRA -> BGR
                except (BrokenPipeError, OSError, ValueError):
                    proc = self._close(proc)     # encoder died; reopen next loop
                rest = interval - (time.perf_counter() - loop)
                if rest > 0:
                    time.sleep(rest)
        finally:
            self._close(proc)
            capture.close()

    def _open(self, w, h):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(self._out_dir, "area_%s.mp4" % stamp)
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", "%dx%d" % (w, h), "-r", str(self._fps), "-i", "-",
            # yuv420p needs even dimensions; scale rounds an odd region down.
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
            "-pix_fmt", "yuv420p", path,
        ]
        try:
            return subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL,
                                    creationflags=_NO_WINDOW)
        except Exception as exc:
            self.error = "ffmpeg failed to start: %s" % exc
            print("[cappa] recorder: " + self.error)
            return None

    @staticmethod
    def _close(proc):
        """Finalize a segment: close ffmpeg's stdin so it writes the MP4
        trailer, then wait briefly. Always returns None (the new proc handle)."""
        if proc is not None:
            try:
                proc.stdin.close()
            except Exception:
                pass
            try:
                proc.wait(timeout=3.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        return None

    def _region(self):
        try:
            return self._region_provider()
        except Exception:
            return None
