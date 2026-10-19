"""Bring the Fortnite client to the front, reliably.

Windows lets a program take the foreground only in some cases, so a background
process (the agents' bridge) is often refused. The order here:

1. already in front: nothing to do;
2. restore it if minimized (never resize: a resize forces the game's swapchain to
   rebuild), then ``SetForegroundWindow``;
3. attach to the foreground window's input thread and switch (the usual workaround);
4. tap Alt, then switch (Alt lifts Windows' foreground lock);
5. one click when the setting allows it: left on the title bar of a windowed client,
   right (aim, nothing lasting) in the middle of a borderless one; the cursor goes back.

The caller has already checked that Fortnite is in play mode; nothing here runs while
it loads. Every attempt reports the method that worked so runs can log it.
"""

from __future__ import annotations

import time
from typing import Any, Callable

SETTLE_S = 0.15


class Win:
    """The real Windows calls. Tests pass a fake with the same methods."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ct, self._wt = ctypes, wintypes
        u = ctypes.WinDLL("user32", use_last_error=True)
        u.GetForegroundWindow.restype = wintypes.HWND
        u.IsIconic.argtypes = [wintypes.HWND]
        u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        u.SetForegroundWindow.argtypes = [wintypes.HWND]
        u.BringWindowToTop.argtypes = [wintypes.HWND]
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
        u.GetWindowThreadProcessId.restype = wintypes.DWORD
        u.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
        u.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        u.GetWindowLongW.restype = ctypes.c_long
        u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        u.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        u.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        u.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        u.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
        self.u = u
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)

    def foreground(self) -> int:
        return int(self.u.GetForegroundWindow() or 0)

    def is_minimized(self, hwnd: int) -> bool:
        return bool(self.u.IsIconic(hwnd))

    def restore(self, hwnd: int) -> None:
        self.u.ShowWindow(hwnd, 9)  # SW_RESTORE: only ever for a minimized window

    def switch(self, hwnd: int) -> None:
        self.u.SetForegroundWindow(hwnd)

    def attach_switch(self, hwnd: int) -> None:
        fg = self.foreground()
        theirs = int(self.u.GetWindowThreadProcessId(fg, None) or 0) if fg else 0
        mine = int(self.k.GetCurrentThreadId())
        attached = bool(theirs and theirs != mine and self.u.AttachThreadInput(mine, theirs, True))
        try:
            self.u.BringWindowToTop(hwnd)
            self.u.SetForegroundWindow(hwnd)
        finally:
            if attached:
                self.u.AttachThreadInput(mine, theirs, False)

    def alt_tap(self) -> None:
        from .inputs import Sender

        s = Sender()
        s.key(0x38, False)  # Alt (scan code), down then up
        s.key(0x38, True)

    def has_title_bar(self, hwnd: int) -> bool:
        WS_CAPTION = 0x00C00000
        return (int(self.u.GetWindowLongW(hwnd, -16)) & WS_CAPTION) == WS_CAPTION

    def title_point(self, hwnd: int) -> tuple[int, int]:
        r = self._wt.RECT()
        self.u.GetWindowRect(hwnd, self._ct.byref(r))
        origin = self._wt.POINT(0, 0)
        self.u.ClientToScreen(hwnd, self._ct.byref(origin))
        # Middle of the strip between the window's top edge and its client area.
        return (r.left + (r.right - r.left) // 2, r.top + max(4, (origin.y - r.top) // 2))

    def middle_point(self, hwnd: int) -> tuple[int, int]:
        r = self._wt.RECT()
        self.u.GetClientRect(hwnd, self._ct.byref(r))
        origin = self._wt.POINT(0, 0)
        self.u.ClientToScreen(hwnd, self._ct.byref(origin))
        return (origin.x + r.right // 2, origin.y + r.bottom // 2)

    def cursor(self) -> tuple[int, int]:
        p = self._wt.POINT(0, 0)
        self.u.GetCursorPos(self._ct.byref(p))
        return (p.x, p.y)

    def set_cursor(self, x: int, y: int) -> None:
        self.u.SetCursorPos(int(x), int(y))

    def click(self, button: str) -> None:
        from .inputs import BUTTONS, Sender

        s = Sender()
        down, up = BUTTONS[button]
        s.button(down)
        time.sleep(0.04)
        s.button(up)


def bring_to_front(hwnd: int, *, allow_click: bool = True, win: Any = None,
                   sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.perf_counter) -> dict[str, Any]:
    """{ok, method, ms, tried} — method: already, plain, attach, alt, click-title, click-right."""
    w = win if win is not None else Win()
    started = clock()
    tried: list[str] = []

    def front() -> bool:
        return w.foreground() == hwnd

    def done(method: str, **extra: Any) -> dict[str, Any]:
        return {"ok": True, "method": method, "ms": int((clock() - started) * 1000), "tried": tried, **extra}

    if front():
        return done("already")
    if w.is_minimized(hwnd):
        w.restore(hwnd)
    for name, attempt in (("plain", lambda: w.switch(hwnd)), ("attach", lambda: w.attach_switch(hwnd)),
                          ("alt", lambda: (w.alt_tap(), w.switch(hwnd)))):
        tried.append(name)
        try:
            attempt()
        except Exception:
            continue
        sleep(SETTLE_S)
        if front():
            return done(name)
    if allow_click:
        titled = False
        try:
            titled = bool(w.has_title_bar(hwnd))
        except Exception:
            titled = False
        point = w.title_point(hwnd) if titled else w.middle_point(hwnd)
        button = "left" if titled else "right"
        name = "click-title" if titled else "click-right"
        tried.append(name)
        back = w.cursor()
        try:
            w.set_cursor(*point)
            w.click(button)
        finally:
            w.set_cursor(*back)
        sleep(SETTLE_S + 0.1)
        if front():
            return done(name, at=list(point), button=button)
    msg = ("Fortnite could not come to the front: click the Fortnite window once, then try again."
           if allow_click else "Fortnite could not come to the front and clicking to focus is off in Settings → Play Tester: "
           "click the Fortnite window once, then try again.")
    return {"ok": False, "method": "refused", "ms": int((clock() - started) * 1000), "tried": tried, "error": msg}
