"""Game windows on screen: the Fortnite client's find and play-mode gate, and for every game
window its focus, drawing area and frames.

The window find and the play-mode gate are Ducky's own Fortnite client tools
(``backend.tools.uefn.fortnite_client``), so Play Tester follows the same safety rule:
nothing is captured or pressed while the client is loading (a mid-load input hung the
GPU on Oct 2 2026). The window is restored only when minimized, never resized.
"""

from __future__ import annotations

import base64
import io
import math
import sys
import time
from typing import Any

SETTLE_S = 15.0
MIN_SIDE = 280
MAX_SIDE = 1568


def client() -> dict[str, Any]:
    """{ok, hwnd, title, foreground, gameplay_loaded, loaded_for_s} or {ok: False, error}."""
    if sys.platform != "win32":
        return {"ok": False, "error": "Play Tester needs Windows (it drives the Fortnite client window)."}
    try:
        from backend.tools.uefn.fortnite_client import client_status
    except Exception:
        return {"ok": False, "error": "This UEFN Ducky has no Fortnite client support: update the app."}
    try:
        status = client_status()
    except Exception as exc:
        return {"ok": False, "error": f"Could not look for the Fortnite client: {type(exc).__name__}"}
    if not status.get("ok"):
        found = str(status.get("error") or "")
        if "found 0" in found:
            return {"ok": False, "error": "No Fortnite client is open: start a session in UEFN (Launch Session) and wait for the island."}
        if "found" in found:
            return {"ok": False, "error": "More than one Fortnite window is open: close the extra one."}
        return {"ok": False, "error": found or "No Fortnite client found"}
    if status.get("running") is False:
        # Apps from Oct 19 2026 answer "no client" as {ok: True, running: False}.
        return {"ok": False, "error": "No Fortnite client is open: start a session in UEFN (Launch Session) and wait for the island."}
    return status


def ready(*, wait: bool = True) -> dict[str, Any]:
    """The client when it is in play mode and settled; waits out the settle time when asked."""
    status = client()
    if not status.get("ok"):
        return status
    if not status.get("gameplay_loaded"):
        return {"ok": False, "error": "Fortnite is still loading the island: wait until it is in play mode (playtester_status shows it).", "loading": True}
    age = float(status.get("loaded_for_s") or 0.0)
    if age < SETTLE_S:
        if not wait:
            return {"ok": False, "error": f"Fortnite just reached the island: ready in {math.ceil(SETTLE_S - age)} s.", "loading": True}
        time.sleep(SETTLE_S - age)
    return status


def _user32() -> Any:
    import ctypes
    from ctypes import wintypes

    u = ctypes.WinDLL("user32", use_last_error=True)
    u.GetForegroundWindow.restype = wintypes.HWND
    u.IsIconic.argtypes = [wintypes.HWND]
    u.SetForegroundWindow.argtypes = [wintypes.HWND]
    u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    u.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    u.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    return u


def foreground() -> int:
    try:
        return int(_user32().GetForegroundWindow() or 0)
    except Exception:
        return 0


def focus(hwnd: int, allow_click: bool = True) -> dict[str, Any]:
    """Bring the client to the front (see focus.py for the order); {ok, method, ms} or {ok: False, error}."""
    from .focus import bring_to_front

    return bring_to_front(hwnd, allow_click=allow_click)


def client_box(hwnd: int) -> tuple[int, int, int, int]:
    """The game's drawing area on screen (no title bar or borders)."""
    import ctypes
    from ctypes import wintypes

    u = _user32()
    rect = wintypes.RECT()
    if not u.GetClientRect(hwnd, ctypes.byref(rect)):
        raise RuntimeError("Could not read the game window size")
    origin = wintypes.POINT(0, 0)
    u.ClientToScreen(hwnd, ctypes.byref(origin))
    box = (origin.x, origin.y, origin.x + rect.right, origin.y + rect.bottom)
    if box[2] - box[0] < MIN_SIDE or box[3] - box[1] < MIN_SIDE:
        raise RuntimeError("The game window is too small to see: make it larger than 280 x 280.")
    return box


def fit(img: Any, longest: int) -> Any:
    """Scale so the longest side is ``longest`` (within Jev's 280-1568) and pad a side under 280."""
    from PIL import Image

    longest = int(min(MAX_SIDE, max(MIN_SIDE, longest)))
    w, h = img.size
    scale = longest / float(max(w, h))
    if abs(scale - 1.0) > 0.01:
        img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.BILINEAR)
    w, h = img.size
    if w < MIN_SIDE or h < MIN_SIDE:
        pad = Image.new("RGB", (max(w, MIN_SIDE), max(h, MIN_SIDE)))
        pad.paste(img, (0, 0))
        img = pad
    return img


def jpeg(img: Any, quality: int = 80) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def grab(hwnd: int, longest: int = 1024) -> dict[str, Any]:
    """One frame of the client: {image (PIL), jpeg, width, height, ms}."""
    from PIL import ImageGrab

    started = time.perf_counter()
    shot = ImageGrab.grab(bbox=client_box(hwnd), all_screens=True)
    img = fit(shot, longest)
    data = jpeg(img)
    return {"image": img, "jpeg": data, "width": img.size[0], "height": img.size[1], "ms": int((time.perf_counter() - started) * 1000)}


def smaller(img: Any, longest: int = 512) -> bytes:
    """The previous frame for 'did it move?': half size is enough and halves the upload."""
    return jpeg(fit(img, longest), quality=70)


def data_uri(data: bytes, width: int = 0) -> str:
    """A JPEG as a data: URI, optionally shrunk to ``width`` px for the tab's thumbnails."""
    if width:
        from PIL import Image

        img = Image.open(io.BytesIO(data))
        if img.size[0] > width:
            img = img.resize((width, max(1, round(img.size[1] * width / img.size[0]))), Image.BILINEAR)
            data = jpeg(img, quality=70)
    return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")


def draw_box(data: bytes, edges: dict[str, float] | None, label: str = "") -> bytes:
    """The frame with Jev's box drawn on it, for the chat's picture of a check or find."""
    if not edges:
        return data
    from PIL import Image, ImageDraw

    img = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = img.size
    box = (edges["left"] * w, edges["top"] * h, edges["right"] * w, edges["bottom"] * h)
    pen = ImageDraw.Draw(img)
    for grow in range(3):
        pen.rectangle((box[0] - grow, box[1] - grow, box[2] + grow, box[3] + grow), outline=(255, 200, 40))
    if label:
        pen.text((box[0] + 4, max(0, box[1] - 14)), label[:40], fill=(255, 200, 40))
    return jpeg(img, quality=85)
