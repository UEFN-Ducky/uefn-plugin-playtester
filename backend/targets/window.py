"""Any game window: found by its program or title, played with real keys and mouse in front.

``UnrealTarget`` and ``RobloxTarget`` are this with a smarter default match and profile.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

from .. import capture, inputs, profiles, screen, windows


class WindowTarget:
    kind = "window"
    needs_focus = True          # real keys: only the window in front gets them (the input lease)
    beacon: Any = None          # world positions, when the game reports them
    recorder_target = ""        # Island Media Kit's capture target for record=true ('' = no recording)
    default_profile = "tps"

    def __init__(self, spec: dict[str, Any]) -> None:
        self.spec = dict(spec or {})
        self.exe = str(self.spec.get("exe") or "").strip()
        self.title = str(self.spec.get("title") or "").strip()
        prof = self.spec.get("profile")
        self.profile = profiles.from_dict(prof) if isinstance(prof, dict) and prof else profiles.make(self.default_profile)
        self.label = str(self.spec.get("label") or "").strip() or self.default_label()
        try:
            self.settle_s = min(60.0, max(0.0, float(self.spec.get("settle_s") or 3.0)))
        except (TypeError, ValueError):
            self.settle_s = 3.0
        self._seen: dict[int, float] = {}

    # ------------------------------------------------------------------ what it is

    def default_label(self) -> str:
        return self.title or os.path.splitext(self.exe)[0] or "Game window"

    def what(self) -> str:
        parts = [f"program {self.exe}" if self.exe else "", f"title containing {self.title!r}" if self.title else ""]
        return " and ".join(p for p in parts if p) or "a window"

    def info(self) -> dict[str, Any]:
        return {"kind": self.kind, "label": self.label, "exe": self.exe or None, "title": self.title or None,
                "profile": self.profile.to_dict(), "needs_focus": self.needs_focus, "positions": self.beacon is not None}

    def place(self) -> str:
        """What runs are about (the island for Fortnite): keys the lessons and memory."""
        return self.label

    def locate(self, label: str) -> dict[str, Any]:
        return {"ok": False, "error": f"{self.label} can't look actors up by label: give target_xyz, or steer by the picture."}

    def landmarks(self) -> list[dict[str, Any]]:
        return []

    def close(self) -> None:
        pass

    # ------------------------------------------------------------------ the window

    def find(self) -> list[dict[str, Any]]:
        return windows.find(self.exe, self.title)

    def status(self) -> dict[str, Any]:
        """{ok, hwnd, title, foreground, gameplay_loaded, loaded_for_s} or {ok: False, error}."""
        try:
            rows = self.find()
        except Exception as exc:
            return {"ok": False, "error": f"Could not list windows: {type(exc).__name__}"}
        if not rows:
            return {"ok": False, "error": f"{self.label} isn't open: start it (Play Tester looks for {self.what()})."}
        row = rows[0]
        hwnd = int(row["hwnd"])
        now = time.time()
        first = self._seen.get(hwnd) or now
        self._seen = {hwnd: first}
        if row.get("width", 0) < screen.MIN_SIDE or row.get("height", 0) < screen.MIN_SIDE:
            if not row.get("minimized"):
                return {"ok": False, "error": f"The {self.label} window is too small to see: make it larger than 280 x 280."}
        return {"ok": True, "hwnd": hwnd, "title": row.get("title"), "foreground": bool(row.get("foreground")),
                "gameplay_loaded": True, "minimized": bool(row.get("minimized")), "loaded_for_s": round(now - first, 1)}

    def ready(self, wait: bool = True) -> dict[str, Any]:
        st = self.status()
        if not st.get("ok"):
            return st
        age = float(st.get("loaded_for_s") or 0.0)
        if age < self.settle_s:
            if not wait:
                return {"ok": False, "error": f"{self.label} just opened: ready in {self.settle_s - age:.0f} s.", "loading": True}
            time.sleep(self.settle_s - age)
        return st

    def focus(self, hwnd: int, allow_click: bool = True) -> dict[str, Any]:
        return screen.focus(hwnd, allow_click)

    def grab(self, hwnd: int, size: int) -> dict[str, Any]:
        """The game in front: a screen grab; behind other windows: its own pixels when it can draw them."""
        if screen.foreground() != int(hwnd):
            started = time.perf_counter()
            img = capture.window_image(hwnd)
            if img is not None:
                img = screen.fit(img, size)
                return {"image": img, "jpeg": screen.jpeg(img), "width": img.size[0], "height": img.size[1],
                        "ms": int((time.perf_counter() - started) * 1000), "covered": True}
        return screen.grab(hwnd, size)

    def smaller(self, image: Any) -> bytes:
        return screen.smaller(image)

    def thumb(self, data: bytes) -> str:
        return screen.data_uri(data, 320)

    def draw_box(self, data: bytes, edges: dict[str, float] | None, label: str = "") -> bytes:
        return screen.draw_box(data, edges, label)

    def controller(self, hwnd: int, stop: threading.Event, counts: float) -> Any:
        p = self.profile
        return inputs.Controller(hwnd, counts_per_90=counts, stop=stop, keymap=p.keys, look_mode=p.look, view=p.view,
                                 box=lambda: screen.client_box(hwnd))


class UnrealTarget(WindowTarget):
    """An Unreal Engine game: a packaged build (…-Win64-Shipping.exe) or the editor's play window."""

    kind = "unreal"
    default_profile = "fps"

    def default_label(self) -> str:
        return self.title or os.path.splitext(self.exe)[0].split("-Win64")[0] or "Unreal game"

    def what(self) -> str:
        return super().what() if (self.exe or self.title) else "a packaged Unreal game or an editor play window"

    def find(self) -> list[dict[str, Any]]:
        if self.exe or self.title:
            return super().find()
        rows = windows.list_windows()
        hits = [r for r in rows if str(r.get("exe") or "").lower().endswith(("-win64-shipping.exe", "-win64-debuggame.exe",
                                                                              "-win64-development.exe"))
                or "preview [netmode" in str(r.get("title") or "").lower()]
        return sorted(hits, key=lambda r: -(int(r.get("width") or 0) * int(r.get("height") or 0)))


class RobloxTarget(WindowTarget):
    """The Roblox player (RobloxPlayerBeta.exe): WASD, Space, E, camera turned with the right mouse button."""

    kind = "roblox"
    default_profile = "roblox"

    def __init__(self, spec: dict[str, Any]) -> None:
        super().__init__({"exe": "RobloxPlayerBeta.exe", **(spec or {})})

    def default_label(self) -> str:
        return self.title or "Roblox"
