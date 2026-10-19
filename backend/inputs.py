"""Keyboard and mouse into the focused Fortnite client, always released.

Keys go in as hardware scan codes and the mouse as relative moves (SendInput), the way
games read them. Every key or button that went down comes up again in a ``finally``,
and a hold stops early the moment Fortnite stops being the foreground window, so
nothing is ever typed into another program.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

MAX_HOLD_S = 3.0
MIN_HOLD_S = 0.05

SCAN: dict[str, int] = {
    "ESC": 0x01, "1": 0x02, "2": 0x03, "3": 0x04, "4": 0x05, "5": 0x06, "6": 0x07, "7": 0x08,
    "8": 0x09, "9": 0x0A, "0": 0x0B, "TAB": 0x0F, "Q": 0x10, "W": 0x11, "E": 0x12, "R": 0x13,
    "T": 0x14, "Y": 0x15, "U": 0x16, "I": 0x17, "O": 0x18, "P": 0x19, "ENTER": 0x1C, "CTRL": 0x1D,
    "A": 0x1E, "S": 0x1F, "D": 0x20, "F": 0x21, "G": 0x22, "H": 0x23, "J": 0x24, "K": 0x25,
    "L": 0x26, "SHIFT": 0x2A, "Z": 0x2C, "X": 0x2D, "C": 0x2E, "V": 0x2F, "B": 0x30, "N": 0x31,
    "M": 0x32, "SPACE": 0x39,
}
ALIASES = {
    "FORWARD": "W", "BACK": "S", "BACKWARD": "S", "LEFT": "A", "RIGHT": "D", "JUMP": "SPACE",
    "INTERACT": "E", "USE": "E", "SPRINT": "SHIFT", "CROUCH": "CTRL", "RELOAD": "R",
    "LSHIFT": "SHIFT", "LCTRL": "CTRL", "CONTROL": "CTRL", "RETURN": "ENTER", "ESCAPE": "ESC",
}
BUTTONS = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010)}


def key_names(keys: Any) -> list[str]:
    """'W+A', 'w a', ['W','space'] -> ['W', 'A'] / ['W', 'SPACE']; raises ValueError on unknown keys."""
    if isinstance(keys, str):
        raw = keys.replace("+", " ").replace(",", " ").split()
    elif isinstance(keys, (list, tuple)):
        raw = [str(k) for k in keys]
    else:
        raw = []
    out: list[str] = []
    for item in raw:
        name = item.strip().upper()
        name = ALIASES.get(name, name)
        if name not in SCAN:
            raise ValueError(f"Unknown key {item!r}: use letters, digits, SPACE, SHIFT, CTRL, TAB, ENTER or ESC")
        if name not in out:
            out.append(name)
    return out


class Sender:
    """The real SendInput calls; tests swap in a recorder with the same four methods."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        pointer = ctypes.c_size_t

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", pointer)]

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", pointer)]

        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

        class UNION(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

        class INPUT(ctypes.Structure):
            _anonymous_ = ("data",)
            _fields_ = [("type", wintypes.DWORD), ("data", UNION)]

        self._ctypes = ctypes
        self._INPUT, self._KEYBDINPUT, self._MOUSEINPUT = INPUT, KEYBDINPUT, MOUSEINPUT
        self._u = ctypes.WinDLL("user32", use_last_error=True)
        self._u.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
        self._u.GetForegroundWindow.restype = wintypes.HWND

    def _send(self, event: Any) -> None:
        if self._u.SendInput(1, self._ctypes.byref(event), self._ctypes.sizeof(self._INPUT)) != 1:
            raise RuntimeError("Windows rejected the input")

    def key(self, scan: int, up: bool) -> None:
        flags = 0x0008 | (0x0002 if up else 0)  # KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP
        self._send(self._INPUT(type=1, ki=self._KEYBDINPUT(0, scan, flags, 0, 0)))

    def move(self, dx: int, dy: int) -> None:
        self._send(self._INPUT(type=0, mi=self._MOUSEINPUT(int(dx), int(dy), 0, 0x0001, 0, 0)))

    def button(self, flag: int) -> None:
        self._send(self._INPUT(type=0, mi=self._MOUSEINPUT(0, 0, 0, flag, 0, 0)))

    def foreground(self) -> int:
        return int(self._u.GetForegroundWindow() or 0)


KEEP_KEYS = frozenset({"W", "SHIFT"})  # what smooth walking may keep held between steps
WATCH_GAP_S = 2.5                       # held keys come up when no new decision arrives this long


class Controller:
    """Presses keys, turns and clicks for one client window; tracks what is down.

    Smooth walking: ``run(actions, smooth=True)`` keeps a plain forward walk (W, with Shift)
    held across steps and turns the mouse while walking, so the player does not stop while
    Jev looks at the next frame. A watchdog lets go of held keys the moment Fortnite loses
    focus, a stop comes in, or no new decision arrives for ``gap`` seconds.
    """

    def __init__(self, hwnd: int, sender: Any = None, *, counts_per_90: float = 2500.0,
                 stop: threading.Event | None = None, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, gap: float = WATCH_GAP_S) -> None:
        self.hwnd = int(hwnd)
        self.sender = sender if sender is not None else Sender()
        self.counts_per_90 = float(counts_per_90) if counts_per_90 and counts_per_90 > 0 else 2500.0
        self.stop = stop or threading.Event()
        self._clock, self._sleep = clock, sleep
        self.gap = float(gap)
        self._down: list[str] = []
        self._buttons: list[str] = []
        self._kept: list[str] = []
        self._refreshed = 0.0
        self._lock = threading.RLock()
        self._watch: threading.Thread | None = None
        self.events: list[dict[str, Any]] = []

    def _focused(self) -> bool:
        return self.sender.foreground() == self.hwnd

    def _up(self, name: str) -> None:
        try:
            self.sender.key(SCAN[name], True)
        except Exception:
            pass
        if name in self._down:
            self._down.remove(name)
        if name in self._kept:
            self._kept.remove(name)

    def release_all(self) -> None:
        with self._lock:
            for name in reversed(list(self._down)):
                self._up(name)
            self._kept.clear()
            for name in reversed(self._buttons):
                try:
                    self.sender.button(BUTTONS[name][1])
                except Exception:
                    pass
            self._buttons.clear()

    def release_kept(self, why: str = "") -> bool:
        with self._lock:
            if not self._kept:
                return False
            for name in reversed(list(self._kept)):
                self._up(name)
            if why:
                self.events.append({"kind": "released", "why": why})
            return True

    def drain_events(self) -> list[dict[str, Any]]:
        out, self.events = self.events, []
        return out

    @property
    def kept(self) -> list[str]:
        return list(self._kept)

    def _wait(self, seconds: float) -> bool:
        """Sleep in small slices; False when focus was lost or a stop came in."""
        end = self._clock() + seconds
        while self._clock() < end:
            if self.stop.is_set() or not self._focused():
                return False
            self._sleep(min(0.02, max(0.0, end - self._clock())))
        return True

    def hold(self, keys: Any, seconds: float) -> dict[str, Any]:
        """Press keys for a time and let go of them (keys already held for walking stay held)."""
        names = key_names(keys)
        if not names:
            raise ValueError("Give keys to press, for example W or W+A")
        seconds = min(MAX_HOLD_S, max(MIN_HOLD_S, float(seconds)))
        if not self._focused():
            raise RuntimeError("Fortnite is not the foreground window; nothing was pressed")
        started = self._clock()
        whole = True
        mine: list[str] = []
        try:
            with self._lock:
                for name in names:
                    if name in self._down:
                        continue
                    self.sender.key(SCAN[name], False)
                    self._down.append(name)
                    mine.append(name)
            whole = self._wait(seconds)
        finally:
            with self._lock:
                for name in reversed(mine):
                    self._up(name)
        out = {"keys": names, "seconds": round(self._clock() - started, 2), "released": True}
        if not whole:
            out["cut_short"] = "stopped" if self.stop.is_set() else "Fortnite lost focus"
            self.release_kept(out["cut_short"])
        return out

    def keep(self, keys: Any) -> dict[str, Any]:
        """Keep exactly these walk keys held (pressing new ones, letting go of others) until the next decision."""
        names = [n for n in key_names(keys) if n in KEEP_KEYS]
        if not names:
            raise ValueError("Only W (and Shift) can be kept held")
        if not self._focused():
            raise RuntimeError("Fortnite is not the foreground window; nothing was pressed")
        with self._lock:
            for name in list(self._kept):
                if name not in names:
                    self._up(name)
            pressed = []
            for name in names:
                if name not in self._down:
                    self.sender.key(SCAN[name], False)
                    self._down.append(name)
                    pressed.append(name)
                if name not in self._kept:
                    self._kept.append(name)
            self._refreshed = self._clock()
        self._ensure_watch()
        return {"keys": names, "held": True, "pressed": pressed}

    def _ensure_watch(self) -> None:
        if self._watch is not None and self._watch.is_alive():
            return

        def watch() -> None:
            while True:
                with self._lock:
                    if not self._kept:
                        return
                    why = ""
                    if self.stop.is_set():
                        why = "stopped"
                    elif not self._focused():
                        why = "Fortnite lost focus"
                    elif self._clock() - self._refreshed > self.gap:
                        why = f"no decision for {self.gap:g} s"
                    if why:
                        self.release_kept(why)
                        return
                time.sleep(0.03)

        self._watch = threading.Thread(target=watch, name="playtester-watch", daemon=True)
        self._watch.start()

    def look(self, dx_deg: float = 0.0, dy_deg: float = 0.0, seconds: float = 0.2) -> dict[str, Any]:
        """Turn the camera: positive dx = right, positive dy = down."""
        dx = int(round(float(dx_deg) / 90.0 * self.counts_per_90))
        dy = int(round(float(dy_deg) / 90.0 * self.counts_per_90))
        if not dx and not dy:
            raise ValueError("Give dx_degrees or dy_degrees to turn")
        if not self._focused():
            raise RuntimeError("Fortnite is not the foreground window; the camera was not turned")
        slices = max(1, min(40, max(abs(dx), abs(dy)) // 120 + 1))
        pause = max(0.0, float(seconds)) / slices
        sent_x = sent_y = 0
        cut = ""
        for i in range(1, slices + 1):
            if self.stop.is_set() or not self._focused():
                cut = "stopped" if self.stop.is_set() else "Fortnite lost focus"
                break
            step_x = round(dx * i / slices) - sent_x
            step_y = round(dy * i / slices) - sent_y
            with self._lock:
                self.sender.move(step_x, step_y)
            sent_x += step_x
            sent_y += step_y
            if pause:
                self._sleep(pause)
        out = {"dx_deg": round(float(dx_deg), 1), "dy_deg": round(float(dy_deg), 1),
               "dx_counts": sent_x, "dy_counts": sent_y, "slices": slices}
        if cut:
            out["cut_short"] = cut
            self.release_kept(cut)
        return out

    def click(self, button: str = "left", seconds: float = 0.05) -> dict[str, Any]:
        name = str(button or "").strip().lower()
        if name not in BUTTONS:
            raise ValueError("Give button: left or right")
        seconds = min(MAX_HOLD_S, max(0.02, float(seconds)))
        if not self._focused():
            raise RuntimeError("Fortnite is not the foreground window; nothing was clicked")
        down, up = BUTTONS[name]
        started = self._clock()
        whole = True
        try:
            with self._lock:
                self.sender.button(down)
                self._buttons.append(name)
            whole = self._wait(seconds)
        finally:
            with self._lock:
                try:
                    self.sender.button(up)
                except Exception:
                    pass
                if name in self._buttons:
                    self._buttons.remove(name)
        out = {"button": name, "seconds": round(self._clock() - started, 2), "released": True}
        if not whole:
            out["cut_short"] = "stopped" if self.stop.is_set() else "Fortnite lost focus"
        return out

    def run(self, actions: list[dict[str, Any]], smooth: bool = False) -> list[dict[str, Any]]:
        """Do a step's actions in order; stops at the first one that is cut short.

        smooth: a plain forward walk stays held for the next step instead of a timed hold;
        a step with no such walk, or with a sharp turn (over 60 degrees), stops walking first.
        """
        actions = list(actions or [])
        walk = None
        if smooth:
            for act in actions:
                keys = act.get("keys")
                if keys and set(key_names(keys)) <= KEEP_KEYS and not act.get("timed"):
                    walk = act
            sharp = any(abs(float(a.get("look") or 0)) > 60 for a in actions)
            if self._kept and (walk is None or sharp):
                self.release_kept("stop to turn" if sharp else "stop for the next move")
        done: list[dict[str, Any]] = []
        for act in actions:
            if self.stop.is_set():
                break
            if act.get("look") is not None or act.get("look_y") is not None:
                res = {"look": self.look(float(act.get("look") or 0), float(act.get("look_y") or 0))}
            elif act.get("keys"):
                if act is walk:
                    res = {"keep": self.keep(act["keys"])}
                else:
                    res = {"press": self.hold(act["keys"], float(act.get("seconds") or 0.1))}
            elif act.get("click"):
                res = {"click": self.click(str(act["click"]), float(act.get("seconds") or 0.05))}
            elif act.get("wait"):
                self._wait(min(2.0, float(act["wait"])))
                res = {"wait": round(min(2.0, float(act["wait"])), 2)}
            else:
                continue
            done.append(res)
            if any(isinstance(v, dict) and v.get("cut_short") for v in res.values()):
                break
        return done
