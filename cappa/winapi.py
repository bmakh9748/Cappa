"""Thin wrappers over the Win32 / DWM calls the overlay needs.

Keeping every ctypes and pywin32 detail in one Qt-free module lets the rest of
the app talk about windows and input in plain terms (``root_window_at``,
``key_down``, ``set_click_through``) instead of raw handles and bit flags."""

import ctypes
from ctypes import wintypes

import win32api
import win32con
import win32gui

# Virtual-key codes for GetAsyncKeyState.
VK_LBUTTON = 0x01
VK_ESCAPE = 0x1B
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt
VK_R = 0x52
VK_X = 0x58

_GA_ROOT = 2
_DWMWA_EXTENDED_FRAME_BOUNDS = 9

# SM_*VIRTUALSCREEN — the bounding box spanning every monitor.
_SM_XVIRTUALSCREEN = 76
_SM_YVIRTUALSCREEN = 77
_SM_CXVIRTUALSCREEN = 78
_SM_CYVIRTUALSCREEN = 79

_DwmGetWindowAttribute = ctypes.windll.dwmapi.DwmGetWindowAttribute
_DwmGetWindowAttribute.restype = ctypes.c_long
_DwmGetWindowAttribute.argtypes = [
    wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD
]


# --- input -----------------------------------------------------------------
def key_down(vk):
    """True while the given virtual-key is physically held, regardless of which
    window currently has focus."""
    return bool(win32api.GetAsyncKeyState(vk) & 0x8000)


def cursor_pos():
    """Cursor position in physical screen pixels: (x, y)."""
    return win32api.GetCursorPos()


# --- windows ---------------------------------------------------------------
def extended_frame_bounds(hwnd):
    """True visible bounds of a window in physical pixels: (l, t, r, b).

    GetWindowRect includes the invisible drop-shadow margin on Win10/11, so an
    overlay sized to it sits slightly proud of the window. DWM's extended frame
    bounds give the real edges. Falls back to GetWindowRect if DWM fails."""
    rect = wintypes.RECT()
    hr = _DwmGetWindowAttribute(
        hwnd, _DWMWA_EXTENDED_FRAME_BOUNDS,
        ctypes.byref(rect), ctypes.sizeof(rect),
    )
    if hr != 0:
        return win32gui.GetWindowRect(hwnd)
    return rect.left, rect.top, rect.right, rect.bottom


def virtual_screen_rect():
    """(x, y, w, h) bounding box across all monitors, in physical pixels."""
    return (
        win32api.GetSystemMetrics(_SM_XVIRTUALSCREEN),
        win32api.GetSystemMetrics(_SM_YVIRTUALSCREEN),
        win32api.GetSystemMetrics(_SM_CXVIRTUALSCREEN),
        win32api.GetSystemMetrics(_SM_CYVIRTUALSCREEN),
    )


def root_window_at(x, y):
    """Top-level window under a physical screen point, or 0 if none."""
    hwnd = win32gui.WindowFromPoint((x, y))
    return win32gui.GetAncestor(hwnd, _GA_ROOT) if hwnd else 0


def foreground_root():
    """Top-level window that currently owns the foreground, or 0."""
    fg = win32gui.GetForegroundWindow()
    return win32gui.GetAncestor(fg, _GA_ROOT) if fg else 0


def is_window(hwnd):
    return bool(win32gui.IsWindow(hwnd))


def is_minimized(hwnd):
    return bool(win32gui.IsIconic(hwnd))


def is_visible(hwnd):
    """True if the window is shown (WS_VISIBLE). Some apps hide their popout
    panels instead of destroying them, so IsWindow alone isn't enough."""
    return bool(win32gui.IsWindowVisible(hwnd))


def is_topmost(hwnd):
    """True if the window is always-on-top (WS_EX_TOPMOST), like a browser's
    picture-in-picture popout. Such windows stay visible without ever owning
    the foreground."""
    style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    return bool(style & win32con.WS_EX_TOPMOST)


def is_above(hwnd, other):
    """True if ``hwnd`` sits above ``other`` in the z-order."""
    cur = win32gui.GetWindow(other, win32con.GW_HWNDPREV)
    while cur:
        if cur == hwnd:
            return True
        cur = win32gui.GetWindow(cur, win32con.GW_HWNDPREV)
    return False


def raise_to_top(hwnd):
    """Move the window to the top of the always-on-top band without activating
    it or changing its geometry."""
    win32gui.SetWindowPos(
        hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
        win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE,
    )


def window_title(hwnd):
    return win32gui.GetWindowText(hwnd) or "window"


def exclude_from_capture(hwnd):
    """Hide the window from screen capture (WDA_EXCLUDEFROMCAPTURE) while it
    stays visible on the monitor. Without this the overlay's own border and
    the launcher land inside the frames the pipeline grabs, polluting the frame
    diff and, later, the OCR. Returns False on Windows < 10 2004, where the
    flag doesn't exist — the overlay still works, just captures itself."""
    _WDA_EXCLUDEFROMCAPTURE = 0x11
    return bool(ctypes.windll.user32.SetWindowDisplayAffinity(
        hwnd, _WDA_EXCLUDEFROMCAPTURE
    ))


def set_click_through(hwnd, enabled):
    """Add or remove WS_EX_TRANSPARENT so the window ignores (or captures) the
    mouse. WS_EX_LAYERED stays on either way for the translucent surface."""
    style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    if enabled:
        style |= win32con.WS_EX_TRANSPARENT
    else:
        style &= ~win32con.WS_EX_TRANSPARENT
    win32gui.SetWindowLong(
        hwnd, win32con.GWL_EXSTYLE, style | win32con.WS_EX_LAYERED
    )
