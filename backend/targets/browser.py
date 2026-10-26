"""A web game in one of the Browser plugin's page tabs (a site, a localhost dev server, a Godot or
Unity web export).

The tab is opened with the Browser plugin's own ``browser_new_tab`` (so its rules for sites apply).
Frames and input then go straight to that tab through the page view's DevTools channel, which
works with the tab in the background, so web testers never wait for the input lease. The page
views live in Ducky's window process: ``web_op`` runs there (``appcall``), with a fixed list of
DevTools methods and no page script.

Browsers send no relative mouse moves to pointer-locked games, so web games suit the side,
top-down and point-and-click profiles; a mouse-look game turns by dragging.
"""

from __future__ import annotations

import base64
import io
import threading
import time
from typing import Any

from .. import appcall, inputs, screen
from .window import WindowTarget

# Our key names -> (DOM key, DOM code, Windows virtual key code).
_KEYS: dict[str, tuple[str, str, int]] = {
    **{c: (c.lower(), f"Key{c}", ord(c)) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"},
    **{d: (d, f"Digit{d}", ord(d)) for d in "0123456789"},
    "SPACE": (" ", "Space", 32), "ENTER": ("Enter", "Enter", 13), "ESC": ("Escape", "Escape", 27),
    "TAB": ("Tab", "Tab", 9), "SHIFT": ("Shift", "ShiftLeft", 16), "CTRL": ("Control", "ControlLeft", 17),
    "ARROWUP": ("ArrowUp", "ArrowUp", 38), "ARROWDOWN": ("ArrowDown", "ArrowDown", 40),
    "ARROWLEFT": ("ArrowLeft", "ArrowLeft", 37), "ARROWRIGHT": ("ArrowRight", "ArrowRight", 39),
}
_NAME_OF = {v: k for k, v in inputs.SCAN.items()}
_BUTTON = {0x0002: ("left", "mousePressed"), 0x0004: ("left", "mouseReleased"),
           0x0008: ("right", "mousePressed"), 0x0010: ("right", "mouseReleased")}


def web_op(op: str, pane: str = "", args: dict[str, Any] | None = None) -> dict[str, Any]:
    """One fixed operation on a Browser page tab; runs in Ducky's window process."""
    from frontend.ui_web import browser_overlay as bo

    args = args if isinstance(args, dict) else {}
    pane = str(pane or "")
    if op == "alive":
        return {"ok": True, "alive": any(p.get("pane_id") == pane and p.get("ready") for p in bo.list_panes())}
    if op == "capture":
        got = bo.devtools(pane, "Page.captureScreenshot", {"format": "jpeg", "quality": 80}, timeout_s=15.0)
        data = str((got.get("reply") or {}).get("data") or "")
        return {"ok": True, "data": data} if got.get("ok") and data else {"ok": False, "error": str(got.get("error") or "no picture")}
    if op == "metrics":
        got = bo.devtools(pane, "Page.getLayoutMetrics", {}, timeout_s=10.0)
        vp = (got.get("reply") or {}).get("cssVisualViewport") or (got.get("reply") or {}).get("layoutViewport") or {}
        w, h = vp.get("clientWidth"), vp.get("clientHeight")
        return {"ok": True, "width": float(w), "height": float(h)} if got.get("ok") and w and h else {"ok": False, "error": "no page size"}
    if op == "key":
        name = str(args.get("name") or "")
        if name not in _KEYS:
            return {"ok": False, "error": f"no key {name}"}
        key, code, vk = _KEYS[name]
        up = bool(args.get("up"))
        params: dict[str, Any] = {"type": "keyUp" if up else ("keyDown" if len(key) == 1 else "rawKeyDown"),
                                  "key": key, "code": code, "windowsVirtualKeyCode": vk}
        if not up and len(key) == 1:
            params["text"] = key
        got = bo.devtools(pane, "Input.dispatchKeyEvent", params, timeout_s=5.0)
        return {"ok": bool(got.get("ok")), "error": got.get("error")}
    if op == "mouse":
        kind = str(args.get("type") or "")
        if kind not in ("mouseMoved", "mousePressed", "mouseReleased"):
            return {"ok": False, "error": f"no mouse event {kind}"}
        params = {"type": kind, "x": float(args.get("x") or 0), "y": float(args.get("y") or 0)}
        button = str(args.get("button") or "")
        if button in ("left", "right"):
            params.update(button=button, clickCount=1 if kind != "mouseMoved" else 0)
            if kind == "mouseMoved":
                params["buttons"] = 1 if button == "left" else 2
        got = bo.devtools(pane, "Input.dispatchMouseEvent", params, timeout_s=5.0)
        return {"ok": bool(got.get("ok")), "error": got.get("error")}
    if op == "close":
        return bo.close_pane(pane)
    return {"ok": False, "error": f"unknown op {op}"}


class BrowserSender:
    """The ``inputs.Controller`` sender for a browser tab: DOM key and mouse events by DevTools."""

    def __init__(self, target: "BrowserTarget") -> None:
        self.t = target
        self.pos = (0.0, 0.0)
        self.held = ""  # the mouse button down, for drags

    def key(self, scan: int, up: bool) -> None:
        self.t.op("key", {"name": _NAME_OF[scan], "up": up})

    def move(self, dx: int, dy: int) -> None:
        # No relative moves in a page: move the pointer (a drag while a button is down), kept on the page.
        w, h = self.t.size()
        self.pos = (min(w - 1, max(0.0, self.pos[0] + dx * 0.25)), min(h - 1, max(0.0, self.pos[1] + dy * 0.25)))
        self.t.op("mouse", {"type": "mouseMoved", "x": self.pos[0], "y": self.pos[1], "button": self.held})

    def move_to(self, x: int, y: int) -> None:
        self.pos = (float(x), float(y))
        self.t.op("mouse", {"type": "mouseMoved", "x": x, "y": y})

    def button(self, flag: int) -> None:
        button, kind = _BUTTON[flag]
        self.held = button if kind == "mousePressed" else ""
        self.t.op("mouse", {"type": kind, "x": self.pos[0], "y": self.pos[1], "button": button})

    def foreground(self) -> int:
        return 0  # a page tab takes input in the background


class BrowserTarget(WindowTarget):
    kind = "web"
    needs_focus = False
    default_profile = "side2d"

    def __init__(self, spec: dict[str, Any], *, api: Any = None) -> None:
        self.api = api
        self.url = str((spec or {}).get("url") or "").strip()
        self.pane = str((spec or {}).get("pane") or "").strip()
        super().__init__(spec)
        self._size: tuple[float, float] | None = None
        self._first: float | None = None

    def default_label(self) -> str:
        host = self.url.split("//", 1)[-1].split("/", 1)[0] if self.url else ""
        return f"Web · {host}" if host else "Web game"

    def info(self) -> dict[str, Any]:
        return {**super().info(), "url": self.url, "pane": self.pane or None}

    def op(self, op: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        out = appcall.run("web_op", web_op, {"op": op, "pane": self.pane, "args": args or {}})
        if not out.get("ok"):
            raise RuntimeError(f"The browser tab didn't take it: {out.get('error') or op}")
        return out

    def open(self) -> dict[str, Any]:
        """Open the page in a new Browser tab (the Browser plugin decides whether the site is allowed)."""
        if not self.url:
            return {"ok": False, "error": "Give the web game's address, for example web:localhost:8060."}
        try:
            out = self.api.call_tool("browser_new_tab", {"url": self.url})
        except Exception as exc:
            return {"ok": False, "error": f"The Browser plugin didn't open {self.url}: {str(exc)[:200]}. Install and turn on Web Browser."}
        pane = str((out or {}).get("pane_id") or "") if isinstance(out, dict) else ""
        if not pane:
            return {"ok": False, "error": str((out or {}).get("error") if isinstance(out, dict) else out)[:200] or "no tab"}
        self.pane = pane
        self.spec["pane"] = pane
        return {"ok": True, "pane": pane}

    def close(self) -> None:
        if self.pane:
            try:
                self.api.call_tool("browser_close_tab", {"pane_id": self.pane})
            except Exception:
                pass

    def size(self) -> tuple[float, float]:
        if self._size is None:
            m = self.op("metrics")
            self._size = (float(m["width"]), float(m["height"]))
        return self._size

    def status(self) -> dict[str, Any]:
        if not self.pane:
            return {"ok": False, "error": f"{self.label} has no browser tab yet."}
        try:
            alive = appcall.run("web_op", web_op, {"op": "alive", "pane": self.pane, "args": {}})
        except Exception as exc:
            return {"ok": False, "error": f"Could not reach the Browser tab: {type(exc).__name__}"}
        if not alive.get("ok") or not alive.get("alive"):
            self._first = None
            return {"ok": False, "error": f"The browser tab for {self.label} is closed: close this tester and open it again."}
        now = time.time()
        self._first = self._first or now
        return {"ok": True, "hwnd": 0, "title": self.label, "foreground": True, "gameplay_loaded": True,
                "loaded_for_s": round(now - self._first, 1)}

    def focus(self, hwnd: int, allow_click: bool = True) -> dict[str, Any]:
        return {"ok": True, "method": "devtools", "ms": 0}

    def grab(self, hwnd: int, size: int) -> dict[str, Any]:
        from PIL import Image

        started = time.perf_counter()
        data = base64.b64decode(self.op("capture")["data"])
        img = Image.open(io.BytesIO(data)).convert("RGB")
        self._size = None  # the page may have resized
        img = screen.fit(img, size)
        return {"image": img, "jpeg": screen.jpeg(img), "width": img.size[0], "height": img.size[1],
                "ms": int((time.perf_counter() - started) * 1000)}

    def controller(self, hwnd: int, stop: threading.Event, counts: float) -> Any:
        p = self.profile
        look = "rmb_drag" if p.look == "mouse" else p.look  # a page turns its camera by dragging
        return inputs.Controller(0, BrowserSender(self), counts_per_90=counts, stop=stop, keymap=p.keys,
                                 look_mode=look, view=p.view, box=lambda: (0, 0, *map(int, self.size())))
