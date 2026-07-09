"""source_wiring.live_video_id: on-screen captions are attributed to a video
ONLY while the extension reports it live and playing. Stale reports (tab
hidden/closed/not YouTube) or a paused frame must yield no id, so nothing
gets logged against a video that isn't really the one on screen."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cappa.ui.source_wiring import live_video_id

VID = "abc123"

# Fresh, playing report -> the video id.
assert live_video_id({"videoId": VID, "paused": False}, VID) == VID
# Stale: bridge.current() returns None once reports stop (tab hidden/closed,
# or a non-YouTube tab). Whatever is on screen is NOT this video.
assert live_video_id(None, VID) is None
# Paused: a frozen frame is not caption life.
assert live_video_id({"videoId": VID, "paused": True}, VID) is None
# A report with no paused key is treated as playing (older payloads).
assert live_video_id({"videoId": VID}, VID) == VID
# No id known yet, even with a fresh report.
assert live_video_id({"paused": False}, None) is None
print("PASS source gate: captions attach to a video only while it plays live")
