"""Top-level windows a tester can drive: list them, find one again by its program or title.

A game window is found again on every step (``find``), because its handle changes when the game
restarts. ``pick`` is the pure part the tests cover; ``list_windows`` asks Windows.
"""

from __future__ import annotations

import os
import sys
from typing import Any

MIN_W, MIN_H = 200, 150
# Windows that are never a game: the shell, Ducky itself, editors and browsers' own frames.
SKIP_EXES = frozenset({"explorer.exe", "uefn-ducky.exe", "applicationframehost.exe", "shellexperiencehost.exe",
                       "searchhost.exe", "startmenuexperiencehost.exe", "textinputhost.exe", "systemsettings.exe"})


def pick(rows: list[dict[str, Any]], exe: str = "", title: str = "") -> list[dict[str, Any]]:
    """The rows matching a program name (exact, any case) and/or a piece of the title; largest first."""
    exe, title = (exe or "").strip().lower(), (title or "").strip().lower()
    if not exe and not title:
        return []
    hits = [r for r in rows
            if (not exe or str(r.get("exe") or "").lower() == exe)
            and (not title or title in str(r.get("title") or "").lower())]
    return sorted(hits, key=lambda r: -(int(r.get("width") or 0) * int(r.get("height") or 0)))


def games(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows worth offering as something to test: titled, big enough, not the shell or Ducky."""
    out = [r for r in rows if r.get("title") and str(r.get("exe") or "").lower() not in SKIP_EXES
           and int(r.get("width") or 0) >= MIN_W and int(r.get("height") or 0) >= MIN_H]
    return sorted(out, key=lambda r: (str(r.get("exe") or "").lower(), str(r.get("title") or "").lower()))


def _user32() -> Any:
    import ctypes
    from ctypes import wintypes

    u = ctypes.WinDLL("user32", use_last_error=True)
    u.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
    u.IsWindowVisible.argtypes = [wintypes.HWND]
    u.IsIconic.argtypes = [wintypes.HWND]
    u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    u.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    u.GetWindow.restype = wintypes.HWND
    u.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    u.GetForegroundWindow.restype = wintypes.HWND
    return u


def _exe_of(pid: int) -> str:
    import ctypes
    from ctypes import wintypes

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = wintypes.HANDLE
    handle = k.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if not k.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        return os.path.basename(buf.value)
    finally:
        k.CloseHandle(handle)


def _cloaked(hwnd: int) -> bool:
    """A window Windows keeps hidden (another virtual desktop, a suspended store app)."""
    import ctypes
    from ctypes import wintypes

    try:
        value = wintypes.DWORD(0)
        ctypes.WinDLL("dwmapi").DwmGetWindowAttribute(wintypes.HWND(hwnd), 14, ctypes.byref(value), ctypes.sizeof(value))
        return bool(value.value)
    except Exception:
        return False


def list_windows() -> list[dict[str, Any]]:
    """Every visible, unowned top-level window: {hwnd, title, exe, pid, width, height, minimized, foreground}."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    u = _user32()
    front = int(u.GetForegroundWindow() or 0)
    rows: list[dict[str, Any]] = []

    def visit(hwnd: Any, _lp: Any) -> bool:
        h = int(hwnd or 0)
        if not h or not u.IsWindowVisible(h) or u.GetWindow(h, 4) or _cloaked(h):  # 4 = GW_OWNER
            return True
        length = u.GetWindowTextLengthW(h)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        u.GetWindowTextW(h, buf, length + 1)
        pid = wintypes.DWORD(0)
        u.GetWindowThreadProcessId(h, ctypes.byref(pid))
        rect = wintypes.RECT()
        u.GetClientRect(h, ctypes.byref(rect))
        rows.append({"hwnd": h, "title": buf.value, "exe": _exe_of(pid.value), "pid": int(pid.value),
                     "width": int(rect.right), "height": int(rect.bottom), "minimized": bool(u.IsIconic(h)),
                     "foreground": h == front})
        return True

    u.EnumWindows(ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(visit), 0)
    return rows


def find(exe: str = "", title: str = "") -> list[dict[str, Any]]:
    return pick(list_windows(), exe, title)
