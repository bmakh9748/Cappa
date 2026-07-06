"""YouTube caption-track source.

Turns "which video am I watching" into exact caption timing for a card. The
on-screen OCR stays the source of a line's *text* (it works even when a video
has no captions); this package supplies the *timing*: fetch the video's caption
track (manual preferred, else auto) with yt-dlp, align an OCR line to it by
similarity, and cut the card's audio from the downloaded track at the matched
[start, end] -- which works while paused and on any past line.

  vtt.py         parse WebVTT (manual + auto rolling formats) -> timed tokens
  transcript.py  Transcript model + OCR-line -> caption-window aligner
  youtube.py     yt-dlp fetch of metadata/captions/audio + ffmpeg slicing

vtt/transcript are pure and import-cheap; youtube.py lazy-imports yt-dlp, so
importing this package never requires the network or yt-dlp."""

from .session import SourceSession
from .transcript import Transcript
from .vtt import Token, parse_vtt

__all__ = ["SourceSession", "Transcript", "Token", "parse_vtt"]
