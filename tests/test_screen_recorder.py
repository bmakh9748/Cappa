"""Unit test: the area recorder's 5 GB self-cap (prune_recordings).

Windowless and encoder-free — it never touches ffmpeg or mss. It only checks
the disk-cap bookkeeping that keeps recordings/ from ever growing without
bound: oldest segments go first, and a folder already under the cap is left
alone."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cappa.screen_recorder import prune_recordings


def test_prune_oldest_first():
    with tempfile.TemporaryDirectory() as tmp:
        # Four 100-byte segments, oldest to newest by mtime.
        for i, name in enumerate(["area_1.mp4", "area_2.mp4",
                                  "area_3.mp4", "area_4.mp4"]):
            p = os.path.join(tmp, name)
            with open(p, "wb") as f:
                f.write(b"x" * 100)
            os.utime(p, (1000 + i, 1000 + i))
        # Cap of 250 bytes must drop the two oldest, leaving 200.
        removed = prune_recordings(out_dir=tmp, max_bytes=250)
        left = sorted(os.listdir(tmp))
        assert removed == 2 and left == ["area_3.mp4", "area_4.mp4"], (
            removed, left)
        # Already under the cap: nothing else removed.
        assert prune_recordings(out_dir=tmp, max_bytes=250) == 0
        # A missing folder is fail-soft, not an error.
        assert prune_recordings(out_dir=os.path.join(tmp, "gone")) == 0
        print("PASS prune: recordings trimmed oldest-first to the 5 GB-style cap")


if __name__ == "__main__":
    test_prune_oldest_first()
