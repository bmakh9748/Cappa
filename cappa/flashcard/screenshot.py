"""Screenshot capture for flashcard drafts."""


def capture_png(region):
    """Return PNG bytes for the tracked area (physical left, top, width, height)."""
    import mss
    import mss.tools

    left, top, width, height = region
    with mss.mss() as sct:
        shot = sct.grab({
            "left": int(left),
            "top": int(top),
            "width": int(width),
            "height": int(height),
        })
    return mss.tools.to_png(shot.rgb, shot.size)


def write_region_png(region, path):
    """Capture the tracked area and write it to path as PNG."""
    write_png_bytes(path, capture_png(region))


def write_png_bytes(path, data):
    with open(path, "wb") as f:
        f.write(data)
