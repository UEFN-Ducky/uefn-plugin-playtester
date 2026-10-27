"""What a tester plays. Every target has the same calls, so the pilot doesn't care which game it is:

- ``status()`` / ``ready(wait)``: {ok, hwnd, foreground, gameplay_loaded, loaded_for_s} or {ok: False, error}
- ``focus(hwnd)``, ``grab(hwnd, size)``, ``smaller``, ``thumb``, ``draw_box``
- ``controller(hwnd, stop, counts)``: an ``inputs.Controller`` with the game's keys and camera
- ``profile`` (profiles.Profile), ``label``, ``kind``, ``spec`` (what is saved to open it again)
- ``needs_focus``: real keys that only reach the window in front (then the input lease applies)
- ``beacon``: world positions for steering (Fortnite's Verse beacon), else None
- ``place()``, ``locate(label)``, ``landmarks()``: what runs are about and level targets
- ``recorder_target``: Island Media Kit's capture target for record=true ('' = can't record)
"""

from __future__ import annotations

from typing import Any, Callable

from .browser import BrowserTarget, web_op
from .fortnite import FortniteTarget
from .godot import GodotTarget
from .window import RobloxTarget, UnrealTarget, WindowTarget

KINDS = ("fortnite", "godot", "unreal", "roblox", "web", "window")
# Programs that are a preset's game: their window opens that preset's tester, never a plain window one.
PRESET_EXES = {"fortniteclient-win64-shipping.exe": "fortnite", "robloxplayerbeta.exe": "roblox"}

__all__ = ["KINDS", "PRESET_EXES", "BrowserTarget", "FortniteTarget", "GodotTarget", "RobloxTarget", "UnrealTarget",
           "WindowTarget", "for_window", "make", "parse", "presets", "web_op"]


def for_window(row: dict[str, Any]) -> tuple[str, str]:
    """(target, kind) for an open window: a preset when it is that game's program, else window:<exe>."""
    exe = str(row.get("exe") or "").strip()
    kind = PRESET_EXES.get(exe.lower())
    if kind:
        return kind, kind
    return f"window:{exe or row.get('title') or ''}", "window"


def parse(target: Any) -> dict[str, Any]:
    """'fortnite' | 'godot[:<project folder>]' | 'unreal[:<exe or title>]' | 'roblox' | 'web:<url>' (or a bare
    http(s)/localhost address) | 'window:<exe or title>' -> a target spec. A dict is taken as a spec."""
    if isinstance(target, dict):
        spec = dict(target)
        if str(spec.get("kind") or "") not in KINDS:
            raise ValueError(f"Unknown target kind {spec.get('kind')!r}: use one of {', '.join(KINDS)}")
        return spec
    text = str(target or "").strip()
    if not text:
        return {"kind": "fortnite"}
    low = text.lower()
    if low.startswith(("http://", "https://", "localhost", "127.0.0.1")):
        return {"kind": "web", "url": text}
    kind, _, rest = text.partition(":")
    kind, rest = kind.strip().lower(), rest.strip()
    if kind == "fortnite":
        return {"kind": "fortnite"}
    if kind == "roblox":
        return {"kind": "roblox", **({"title": rest} if rest else {})}
    if kind == "godot":
        return {"kind": "godot", **({"project": rest} if rest else {})}
    if kind in ("unreal", "window"):
        if kind == "window" and not rest:
            raise ValueError("Say which window: window:MyGame.exe or window:<part of its title>")
        match = {"exe": rest} if rest.lower().endswith(".exe") else ({"title": rest} if rest else {})
        return {"kind": kind, **match}
    if kind == "web":
        if not rest:
            raise ValueError("Give the web game's address: web:localhost:8060 or web:https://example.com/game")
        return {"kind": "web", "url": rest}
    raise ValueError(f"Unknown target {text!r}: use fortnite, godot, unreal, roblox, web:<url> or window:<exe or title>")


def make(spec: dict[str, Any], *, api: Any = None, data: Any = None,
         island: Callable[[], str] | None = None) -> Any:
    kind = str(spec.get("kind") or "fortnite")
    if kind == "fortnite":
        return FortniteTarget(spec, api=api, data=data, island=island)
    if kind == "godot":
        return GodotTarget(spec, api=api)
    if kind == "web":
        return BrowserTarget(spec, api=api)
    if kind == "unreal":
        return UnrealTarget(spec)
    if kind == "roblox":
        return RobloxTarget(spec)
    return WindowTarget(spec)


def presets() -> list[dict[str, Any]]:
    """The games the New tester menu offers before the list of open windows."""
    return [
        {"target": "fortnite", "kind": "fortnite", "label": "Fortnite", "hint": "The UEFN session's client"},
        {"target": "godot", "kind": "godot", "label": "Godot", "hint": "Game run from the editor with the runtime probe"},
        {"target": "unreal", "kind": "unreal", "label": "Unreal", "hint": "Packaged game or the editor's play window"},
        {"target": "roblox", "kind": "roblox", "label": "Roblox", "hint": "The Roblox player"},
        {"target": "web:", "kind": "web", "label": "Web page", "hint": "A web game or a localhost dev server", "needs_url": True},
    ]
