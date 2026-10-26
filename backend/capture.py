"""A window's own pixels, even when another window covers it (PrintWindow, full content).

Used for the window picker's thumbnails and for frames of a covered window. PrintWindow asks
the window to draw itself, so a hung window would block forever: it runs on a worker thread
with a timeout, and windows Windows reports as hung are skipped. Games drawn with some GPU APIs
come back black; ``black`` says so and the caller falls back to a screen grab in front.
"""

from __future__ import annotations

import sys
import threading
from typing import Any

PW_CLIENT_FULL = 0x1 | 0x2  # PW_CLIENTONLY | PW_RENDERFULLCONTENT


def black(img: Any) -> bool:
    """Nothing drawn: every pixel nearly black."""
    try:
        return img.convert("L").getextrema()[1] < 12
    except Exception:
        return True


def _print_window(hwnd: int) -> Any:
    import ctypes
    from ctypes import wintypes

    from PIL import Image

    u = ctypes.WinDLL("user32", use_last_error=True)
    g = ctypes.WinDLL("gdi32", use_last_error=True)
    u.GetDC.restype = wintypes.HDC
    u.GetDC.argtypes = [wintypes.HWND]
    u.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    u.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    u.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    g.CreateCompatibleDC.restype = wintypes.HDC
    g.CreateCompatibleDC.argtypes = [wintypes.HDC]
    g.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    g.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    g.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    g.SelectObject.restype = wintypes.HGDIOBJ
    g.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    g.DeleteDC.argtypes = [wintypes.HDC]
    g.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                            ctypes.c_void_p, wintypes.UINT]

    rect = wintypes.RECT()
    if not u.GetClientRect(hwnd, ctypes.byref(rect)) or rect.right <= 0 or rect.bottom <= 0:
        return None
    w, h = int(rect.right), int(rect.bottom)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

    hdc = u.GetDC(hwnd)
    if not hdc:
        return None
    mem = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, w, h)
    old = g.SelectObject(mem, bmp)
    try:
        if not u.PrintWindow(hwnd, mem, PW_CLIENT_FULL):
            return None
        info = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)  # top-down BGRA
        buf = ctypes.create_string_buffer(w * h * 4)
        if g.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(info), 0) != h:
            return None
        return Image.frombuffer("RGB", (w, h), buf.raw, "raw", "BGRX", 0, 1)
    finally:
        g.SelectObject(mem, old)
        g.DeleteObject(bmp)
        g.DeleteDC(mem)
        u.ReleaseDC(hwnd, hdc)


def hung(hwnd: int) -> bool:
    try:
        import ctypes

        return bool(ctypes.WinDLL("user32").IsHungAppWindow(int(hwnd)))
    except Exception:
        return False


def window_image(hwnd: int, timeout_s: float = 2.0, *, grab: Any = None) -> Any:
    """The window's client area as a PIL image, or None (hung, timed out, black, not Windows)."""
    if sys.platform != "win32" and grab is None:
        return None
    if grab is None and hung(hwnd):
        return None
    box: dict[str, Any] = {}

    def work() -> None:
        try:
            box["img"] = (grab or _print_window)(int(hwnd))
        except Exception:
            box["img"] = None

    # ponytail: a timed-out PrintWindow keeps its daemon thread until the window answers; one per call.
    t = threading.Thread(target=work, name="playtester-printwindow", daemon=True)
    t.start()
    t.join(timeout_s)
    img = box.get("img")
    if t.is_alive() or img is None or black(img):
        return None
    return img


def thumbnail(hwnd: int, width: int = 240) -> str:
    """A small JPEG data URI of the window for the picker, or '' when it can't be seen."""
    import base64
    import io

    img = window_image(hwnd, 0.8)
    if img is None:
        return ""
    img = img.copy()
    img.thumbnail((width, width))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=70)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
