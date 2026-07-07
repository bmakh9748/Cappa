"""The Cappa logo — exploration 1b "Caption tile" (see "Cappa Logo
Explorations.dc.html" in the repo root): a red rounded tile holding a
two-bar subtitle. Drawn as paint code from its 128px design geometry, so
every size is rendered crisp rather than scaled from a bitmap.

paint_tile() draws it into any rect on an active painter (the launcher
icon paints through it live); app_icon() renders it into a multi-size
QIcon for window title bars; write_ico() saves the same renders as a
.ico file for the Start Menu shortcut the taskbar takes its icon from
(this Windows 11 taskbar never asks the window)."""

import struct

from PySide6.QtCore import QBuffer, QIODevice, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

RED = (208, 67, 59)  # #D0433B — the 1b original red
# The two caption bars, (left, bottom, width, alpha) in design px: a full
# line, then a shorter 75%-white one beneath it.
_BARS = ((26, 44, 76, 255), (41, 22, 46, 191))
_BAR_H = 15
_DESIGN = 128.0


def paint_tile(p, r, alpha=255):
    """Draw the tile filling rect `r`. `alpha` fades the red fill only —
    the bars keep their own opacity (the launcher's hover dim)."""
    s = r.width() / _DESIGN
    p.setPen(QColor(255, 255, 255, 40))
    p.setBrush(QColor(*RED, alpha))
    p.drawRoundedRect(r, 30 * s, 30 * s)
    p.setPen(Qt.NoPen)
    for left, bottom, width, bar_alpha in _BARS:
        bar = QRectF(r.left() + left * s,
                     r.top() + (_DESIGN - bottom - _BAR_H) * s,
                     width * s, _BAR_H * s)
        p.setBrush(QColor(255, 255, 255, bar_alpha))
        p.drawRoundedRect(bar, 8 * s, 8 * s)


_ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def _render(size):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    paint_tile(p, QRectF(0, 0, size, size))
    p.end()
    return pm


def app_icon():
    """The logo as a QIcon, rendered at every size Windows asks for
    (title bar, alt-tab)."""
    icon = QIcon()
    for size in _ICON_SIZES:
        icon.addPixmap(_render(size))
    return icon


def write_ico(path):
    """Save the logo as a multi-size .ico (PNG-compressed entries, fine
    since Vista). Needs a QApplication to exist — QPixmap can't render
    without one."""
    pngs = []
    for size in _ICON_SIZES:
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        _render(size).save(buf, "PNG")
        buf.close()
        pngs.append(bytes(buf.data()))
    with open(path, "wb") as f:
        f.write(struct.pack("<HHH", 0, 1, len(pngs)))  # ICONDIR, type 1
        offset = 6 + 16 * len(pngs)
        for size, png in zip(_ICON_SIZES, pngs):       # ICONDIRENTRYs
            edge = size if size < 256 else 0
            f.write(struct.pack("<BBBBHHII", edge, edge, 0, 0, 1, 32,
                                len(png), offset))
            offset += len(png)
        for png in pngs:
            f.write(png)
