"""World coordinates: where the target is from where the player stands and looks.

Unreal units are centimetres; X forward, Y right, Z up. Yaw 0 faces +X and grows turning
right, so ``atan2(dy, dx)`` is a yaw too and "target yaw minus player yaw" is how far to
turn (positive = right). Pure maths, no game calls.
"""

from __future__ import annotations

import math
import re
from typing import Any

FLOOR_M = 2.5      # more height difference than this is another floor
ARRIVE_M = 2.0     # this close (and on the same floor) is at the target
AHEAD_DEG = 20.0
BEHIND_DEG = 110.0


def wrap(deg: float) -> float:
    """An angle in (-180, 180]."""
    d = (float(deg) + 180.0) % 360.0 - 180.0
    return 180.0 if d == -180.0 else d


def point(value: Any) -> dict[str, float] | None:
    """{x, y, z} from a dict, a list or 'x,y,z' / 'x y z' text; None when it isn't one."""
    if isinstance(value, dict):
        try:
            return {k: float(value.get(k, value.get(k.upper()))) for k in ("x", "y", "z")}
        except (TypeError, ValueError):
            return None
    if isinstance(value, (list, tuple)) and len(value) == 3:
        try:
            return {"x": float(value[0]), "y": float(value[1]), "z": float(value[2])}
        except (TypeError, ValueError):
            return None
    if isinstance(value, str):
        nums = re.findall(r"-?\d+(?:\.\d+)?", value)
        if len(nums) == 3:
            return {"x": float(nums[0]), "y": float(nums[1]), "z": float(nums[2])}
    return None


def relate(pose: dict[str, Any], target: dict[str, float]) -> dict[str, Any]:
    """Distance (m), height (m, + = above), turn (deg, + = right), side and floor."""
    dx = target["x"] - float(pose["x"])
    dy = target["y"] - float(pose["y"])
    dz = target["z"] - float(pose["z"])
    dist = math.hypot(dx, dy) / 100.0
    turn = wrap(math.degrees(math.atan2(dy, dx)) - float(pose.get("yaw") or 0.0)) if dist > 0.01 else 0.0
    a = abs(turn)
    side = "ahead" if a <= AHEAD_DEG else ("behind" if a > BEHIND_DEG else ("right" if turn > 0 else "left"))
    height = dz / 100.0
    floor = "up" if height > FLOOR_M else ("down" if height < -FLOOR_M else "same")
    return {"distance_m": round(dist, 2), "height_m": round(height, 2), "turn_deg": round(turn, 1),
            "side": side, "floor": floor, "arrived": dist <= ARRIVE_M and abs(height) <= 1.5}


def describe(rel: dict[str, Any]) -> str:
    """'34 m away, behind you (150° right), 4.2 m up (another floor)'."""
    turn = rel["turn_deg"]
    if rel["side"] == "ahead":
        where = "straight ahead" if abs(turn) <= 5 else f"nearly ahead ({abs(turn):g}° {'right' if turn > 0 else 'left'})"
    elif rel["side"] == "behind":
        where = f"behind you ({abs(turn):g}° {'right' if turn > 0 else 'left'})"
    else:
        where = f"{abs(turn):g}° to the {rel['side']}"
    text = f"{rel['distance_m']:g} m away, {where}"
    if rel["floor"] == "up":
        text += f", {rel['height_m']:g} m up (another floor)"
    elif rel["floor"] == "down":
        text += f", {abs(rel['height_m']):g} m down (another floor)"
    return text


def moved_m(a: dict[str, Any] | None, b: dict[str, Any] | None) -> float | None:
    if not a or not b:
        return None
    return round(math.dist((a["x"], a["y"], a["z"]), (b["x"], b["y"], b["z"])) / 100.0, 2)


def nearby(pose: dict[str, Any] | None, marks: list[dict[str, Any]], k: int = 3, within_m: float = 60.0,
           skip: str = "") -> list[dict[str, Any]]:
    """The k nearest landmarks to the player, each with distance, side, turn and height."""
    if not pose:
        return []
    rows = []
    for mk in marks or []:
        pt = point(mk)
        name = str(mk.get("name") or mk.get("label") or "").strip()
        if not pt or not name or (skip and name.lower() == skip.lower()):
            continue
        rel = relate(pose, pt)
        if rel["distance_m"] <= within_m:
            rows.append({"name": name, **pt, **{k2: rel[k2] for k2 in ("distance_m", "turn_deg", "side", "height_m", "floor")},
                         "source": mk.get("source", "")})
    rows.sort(key=lambda r: r["distance_m"])
    return rows[: max(0, k)]


def nearby_text(rows: list[dict[str, Any]]) -> str:
    """'Button_Tower 12 m ahead, 4.5 m up; ItemSpawner 6 m left'."""
    parts = []
    for r in rows:
        side = r["side"] if r["side"] != "behind" else "behind"
        bit = f"{r['name']} {r['distance_m']:.0f} m {side}"
        if r["floor"] != "same":
            bit += f", {abs(r['height_m']):.1f} m {'up' if r['height_m'] > 0 else 'down'}"
        parts.append(bit)
    return "; ".join(parts)
