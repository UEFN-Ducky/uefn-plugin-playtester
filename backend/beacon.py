"""What the island prints: the player's position from the Play Tester beacon, landmarks,
events, and every other Verse print, from the UEFN Output Log.

A live session's Verse ``Print`` lines reach the UEFN Output Log as ``LogVerse: : <text>``.
The beacon device prints ``[PTPOS] tick player x y z yaw pitch [state]`` a few times a
second (state: ground, air, falling, gliding, water) and ``[PTMARK] name x y z`` for its
landmarks; any island can print ``[PTEVENT] <text>`` (or anything else) for Play Tester to
react to. This reads only what was added since the last read (the last 256 KB the first
time), so a step pays for a few lines, not the whole log.
"""

from __future__ import annotations

import calendar
import os
import re
import threading
import time
from collections import deque
from typing import Any

TAG = "[PTPOS]"
FRESH_S = 3.0
KEEP_LINES = 400
_FIRST_READ = 256 * 1024
_NUM = r"(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"
_STAMP = r"^\[(\d{4})\.(\d{2})\.(\d{2})-(\d{2})\.(\d{2})\.(\d{2}):(\d{3})\]"
_LINE = re.compile(_STAMP + r".*?\[PTPOS\] (\d+) (\d+) " + " ".join([_NUM] * 5) + r"(?: ([a-z]+))?")
_MARK = re.compile(_STAMP + r".*?\[PTMARK\] (.+?) " + " ".join([_NUM] * 3) + r"\s*$")
_VERSE = re.compile(_STAMP + r".*?LogVerse:[^:]*: ?(.*)$")


def editor_log() -> str:
    return os.path.join(os.environ.get("LOCALAPPDATA", ""), "UnrealEditorFortnite", "Saved", "Logs", "UnrealEditorFortnite.log")


def _stamp(g: tuple[str, ...]) -> float:
    return calendar.timegm(tuple(int(x) for x in g[:6]) + (0, 0, 0)) + int(g[6]) / 1000.0  # log stamps are UTC


def parse(line: str) -> dict[str, Any] | None:
    m = _LINE.match(line.strip())
    if not m:
        return None
    g = m.groups()
    out = {"t": _stamp(g), "tick": int(g[7]), "player": int(g[8]), "x": float(g[9]), "y": float(g[10]),
           "z": float(g[11]), "yaw": float(g[12]), "pitch": float(g[13])}
    if g[14]:
        out["state"] = g[14]
    return out


def parse_mark(line: str) -> dict[str, Any] | None:
    m = _MARK.match(line.strip())
    if not m:
        return None
    g = m.groups()
    return {"t": _stamp(g), "name": g[7].strip(), "x": float(g[8]), "y": float(g[9]), "z": float(g[10])}


def parse_verse(line: str) -> dict[str, Any] | None:
    """Any Verse print: {t, text}."""
    m = _VERSE.match(line.strip())
    if not m:
        return None
    return {"t": _stamp(m.groups()), "text": m.groups()[7].strip()}


class Beacon:
    """Newest pose per player, landmarks and recent Verse prints; safe from several threads."""

    def __init__(self, path: str = "", clock: Any = time.time) -> None:
        self.path = path or editor_log()
        self.clock = clock
        self._offset: int | None = None
        self._rest = b""
        self._poses: dict[int, dict[str, Any]] = {}
        self._marks: dict[str, dict[str, Any]] = {}
        self._prints: deque[dict[str, Any]] = deque(maxlen=KEEP_LINES)
        self._lock = threading.Lock()

    def _read_new(self) -> None:
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return
        if self._offset is None or size < self._offset:  # first read, or a new log after a restart
            self._offset = max(0, size - _FIRST_READ)
            self._rest = b""
        if size == self._offset:
            return
        with open(self.path, "rb") as fh:
            fh.seek(self._offset)
            chunk = fh.read(size - self._offset)
        self._offset = size
        data = self._rest + chunk
        lines = data.split(b"\n")
        self._rest = lines.pop() if not data.endswith(b"\n") else b""
        for raw in lines:
            if b"LogVerse" not in raw and b"[PT" not in raw:
                continue
            line = raw.decode("utf-8", errors="replace")
            if "[PTPOS]" in line:
                pose = parse(line)
                if pose:
                    self._poses[pose["player"]] = pose
                continue  # positions are not prints anyone waits for
            if "[PTMARK]" in line:
                mark = parse_mark(line)
                if mark:
                    self._marks[mark["name"]] = mark
                continue
            verse = parse_verse(line)
            if verse and verse["text"]:
                self._prints.append(verse)

    def pose(self, player: int = 0) -> dict[str, Any] | None:
        """The newest pose with ``age_s``, or None when the beacon never printed."""
        with self._lock:
            self._read_new()
            pose = self._poses.get(player)
        if not pose:
            return None
        return {**pose, "age_s": round(max(0.0, self.clock() - pose["t"]), 2)}

    def fresh(self, player: int = 0) -> dict[str, Any] | None:
        pose = self.pose(player)
        return pose if pose and pose["age_s"] <= FRESH_S else None

    def marks(self) -> dict[str, dict[str, Any]]:
        """Landmarks the island printed: {name: {x, y, z, t}}."""
        with self._lock:
            self._read_new()
            return {k: dict(v) for k, v in self._marks.items()}

    def prints_since(self, since: float, contains: str = "") -> list[dict[str, Any]]:
        """Verse prints (not positions or marks) logged at or after ``since``, optionally containing text."""
        want = (contains or "").strip().lower()
        with self._lock:
            self._read_new()
            rows = [dict(p) for p in self._prints if p["t"] >= since - 0.5]
        if want:
            rows = [p for p in rows if want in p["text"].lower()]
        return rows
