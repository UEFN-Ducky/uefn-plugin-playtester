"""A Godot game run from the editor with the Godot plugin's runtime probe (godot_call tools).

Frames come from ``get_runtime_screenshot`` and keys, mouse moves and clicks go in as Godot input
events (``simulate_runtime_input_event``), so nothing needs the window in front: Godot testers run
side by side with others and never wait for the input lease. ``where`` reads node positions with
``evaluate_runtime_expression`` (shown to agents; steering by coordinates stays Fortnite's).
"""

from __future__ import annotations

import io
import json
import os
import re
import threading
import time
from typing import Any

from .. import inputs, screen
from .window import WindowTarget

# Godot 4 Key enum: letters and digits are their ASCII codes, the rest are special codes.
_SPECIAL = {"SPACE": 32, "ESC": 4194305, "TAB": 4194306, "ENTER": 4194309, "SHIFT": 4194325, "CTRL": 4194326,
            "ARROWLEFT": 4194319, "ARROWUP": 4194320, "ARROWRIGHT": 4194321, "ARROWDOWN": 4194322}
_NAME_OF = {v: k for k, v in inputs.SCAN.items()}
_BUTTON = {0x0002: (1, True), 0x0004: (1, False), 0x0008: (2, True), 0x0010: (2, False)}
_NUM = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
SETUP = ("Run the game from Godot with the runtime probe: godot_call(tool='install_runtime_probe') once, "
         "then godot_call(tool='run_project').")


def keycode(name: str) -> int:
    if name in _SPECIAL:
        return _SPECIAL[name]
    if len(name) == 1 and name.isalnum():
        return ord(name.upper())
    raise ValueError(f"Godot has no key for {name}")


def numbers(value: Any) -> list[float]:
    """Numbers out of a Godot value: [x, y(, z)] from '(1.5, 2, 3)', a dict or a list."""
    if isinstance(value, dict):
        return [float(value[k]) for k in ("x", "y", "z") if isinstance(value.get(k), (int, float))]
    if isinstance(value, (list, tuple)):
        return [float(v) for v in value if isinstance(v, (int, float))]
    return [float(n) for n in _NUM.findall(str(value or ""))]


def reason(error: Any) -> tuple[str, str]:
    """(message, state) from a probe error, which can be JSON inside JSON inside the text."""
    text, state = str(error or "").strip(), ""
    for _ in range(4):
        try:
            data = json.loads(text)
        except ValueError:
            break
        if not isinstance(data, dict):
            break
        state = state or str(data.get("state") or "")
        inner = data.get("error") or data.get("message")
        if not inner:
            break
        text = str(inner).strip()
    return text, state


def trouble(error: Any) -> dict[str, Any]:
    """A probe error as {title, fix} for the tab and a full sentence (plus how) for agents."""
    text, state = reason(error)
    low = f"{state} {text}".lower()
    if "waiting_for_editor" in low or "no godot editor" in low:
        title, fix = "The Godot editor isn't open", "Open your project in Godot with the MCP add-on, then press Run."
    elif "update the app" in low:
        title, fix = "UEFN Ducky needs an update", "Update UEFN Ducky to test Godot games."
    elif "godot_call" in low and ("unknown" in low or "not found" in low or "no tool" in low):
        title, fix = "The Godot plugin is off", "Turn on Godot in the Store."
    else:
        title, fix = "The Godot game isn't running", "Press Run in the Godot editor."
    return {"ok": False, "error": f"{title}: {fix} {SETUP}", "title": title, "fix": fix, "detail": text[:160]}


def _payload(out: Any) -> Any:
    if isinstance(out, dict):
        if out.get("ok") is False or (out.get("error") and "result" not in out):
            raise RuntimeError(str(out.get("error") or "Godot refused"))
        for key in ("result", "data"):
            if key in out:
                return out[key]
    return out


def _value(out: Any) -> Any:
    data = _payload(out)
    if isinstance(data, dict):
        for key in ("value", "result", "output"):
            if key in data:
                return data[key]
    return data


def _file_in(data: Any) -> str:
    """The first existing picture file named anywhere in a tool's answer."""
    if isinstance(data, str):
        return data if data.lower().endswith((".jpg", ".jpeg", ".png")) and os.path.isfile(data) else ""
    if isinstance(data, dict):
        data = list(data.values())
    if isinstance(data, list):
        for item in data:
            hit = _file_in(item)
            if hit:
                return hit
    return ""


class GodotSender:
    """The ``inputs.Controller`` sender for a Godot game: input events through the runtime probe."""

    def __init__(self, target: "GodotTarget") -> None:
        self.t = target
        self.pos = (0.0, 0.0)

    def key(self, scan: int, up: bool) -> None:
        code = keycode(_NAME_OF[scan])
        self.t.call("simulate_runtime_input_event",
                    {"event": {"type": "key", "keycode": code, "physical_keycode": code, "pressed": not up}})

    def move(self, dx: int, dy: int) -> None:
        self.t.call("simulate_runtime_input_event",
                    {"event": {"type": "mouse_motion", "relative": {"x": dx, "y": dy},
                               "position": {"x": self.pos[0], "y": self.pos[1]}}})

    def move_to(self, x: int, y: int) -> None:
        self.pos = (float(x), float(y))
        self.t.call("simulate_runtime_input_event",
                    {"event": {"type": "mouse_motion", "position": {"x": x, "y": y}, "relative": {"x": 0, "y": 0}}})

    def button(self, flag: int) -> None:
        index, pressed = _BUTTON[flag]
        self.t.call("simulate_runtime_input_event",
                    {"event": {"type": "mouse_button", "button_index": index, "pressed": pressed,
                               "position": {"x": self.pos[0], "y": self.pos[1]}}})

    def foreground(self) -> int:
        return 0  # always "in front": the probe takes input in the background


class GodotTarget(WindowTarget):
    kind = "godot"
    needs_focus = False

    def __init__(self, spec: dict[str, Any], *, api: Any = None) -> None:
        self.api = api
        self.project = str((spec or {}).get("project") or "").strip()
        self.player = str((spec or {}).get("player") or "").strip()
        super().__init__(spec)
        self._first: float | None = None
        self._lock = threading.Lock()

    def default_label(self) -> str:
        name = os.path.basename(self.project.rstrip("/\\")) if self.project else ""
        return f"Godot · {name}" if name else "Godot game"

    def info(self) -> dict[str, Any]:
        return {**super().info(), "project": self.project or None, "player": self.player or None,
                "positions": bool(self.player)}

    def place(self) -> str:
        return self.project or self.label

    def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        if self.api is None or not callable(getattr(self.api, "call_tool", None)):
            raise RuntimeError("This UEFN Ducky can't call the Godot plugin from Play Tester: update the app.")
        args: dict[str, Any] = {"tool": tool, "arguments": arguments}
        if self.project:
            args["project"] = self.project
        with self._lock:  # one probe request at a time
            try:
                return self.api.call_tool("godot_call", args)
            except ValueError as exc:
                raise RuntimeError(str(exc)) from exc

    def status(self) -> dict[str, Any]:
        try:
            info = _payload(self.call("get_runtime_info", {"timeout_ms": 1500}))
        except Exception as exc:
            self._first = None
            return trouble(exc)
        now = time.time()
        self._first = self._first or now
        scene = info.get("current_scene") if isinstance(info, dict) else None
        return {"ok": True, "hwnd": 0, "title": self.label, "foreground": True, "gameplay_loaded": True,
                "loaded_for_s": round(now - self._first, 1), "scene": scene}

    def focus(self, hwnd: int, allow_click: bool = True) -> dict[str, Any]:
        return {"ok": True, "method": "probe", "ms": 0}

    def grab(self, hwnd: int, size: int) -> dict[str, Any]:
        from PIL import Image

        started = time.perf_counter()
        out = _payload(self.call("get_runtime_screenshot", {"format": "jpg", "save_path": "user://playtester_frame.jpg"}))
        path = _file_in(out)
        if not path:
            raise RuntimeError("Godot took the screenshot but didn't say where it saved it.")
        with open(path, "rb") as fh:
            img = Image.open(io.BytesIO(fh.read())).convert("RGB")
        img = screen.fit(img, size)
        return {"image": img, "jpeg": screen.jpeg(img), "width": img.size[0], "height": img.size[1],
                "ms": int((time.perf_counter() - started) * 1000)}

    def viewport(self) -> tuple[int, int, int, int]:
        size = numbers(_value(self.call("evaluate_runtime_expression",
                                        {"expression": "get_viewport().get_visible_rect().size", "node_path": "/root"})))
        if len(size) < 2:
            raise RuntimeError("Godot didn't give the game's size.")
        return 0, 0, int(size[0]), int(size[1])

    def controller(self, hwnd: int, stop: threading.Event, counts: float) -> Any:
        p = self.profile
        return inputs.Controller(0, GodotSender(self), counts_per_90=counts, stop=stop, keymap=p.keys,
                                 look_mode=p.look, view=p.view, box=self.viewport)

    def where(self, target_node: str = "") -> dict[str, Any]:
        """The player node's position (and a target node's, with the distance between) in Godot units."""
        if not self.player:
            return {"ok": False, "error": "Give this tester the player's node path (player=/root/Main/Player) to read positions."}
        try:
            me = numbers(_value(self.call("evaluate_runtime_expression", {"expression": "global_position", "node_path": self.player})))
            out: dict[str, Any] = {"ok": True, "player": me, "units": "Godot units"}
            if target_node:
                it = numbers(_value(self.call("evaluate_runtime_expression",
                                              {"expression": "global_position", "node_path": target_node})))
                out["target"] = it
                if me and len(me) == len(it):
                    out["distance"] = round(sum((a - b) ** 2 for a, b in zip(me, it)) ** 0.5, 2)
            return out
        except Exception as exc:
            return {"ok": False, "error": f"Godot couldn't read the position: {str(exc)[:160]}"}
