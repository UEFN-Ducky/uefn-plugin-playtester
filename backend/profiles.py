"""How a game is played: its name in Jev's questions, how the camera works and which keys do what.

The policy always thinks in Fortnite-style moves (turn the camera, walk forward with W, jump with
SPACE, use with E). A profile turns those into the game's own controls:

- ``mouse``: the mouse turns the camera (first and third person 3D games).
- ``rmb_drag``: the camera turns while the right mouse button is held (Roblox's default camera).
- ``facing``: no camera. A turn changes which way the player faces, and W walks that way:
  left/right in a side view (``side2d``), eight directions seen from above (``topdown``).
- ``none``: no movement at all; the run clicks what it looks for (``pointclick``).

``keys`` maps a logical key to the game's key: {"SPACE": "UP"} for a platformer that jumps with the
up arrow, {"RIGHT": "RIGHT", "LEFT": "LEFT"} for arrow-key walking in a side view.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

VIEWS = ("fps3d", "tps3d", "side2d", "topdown", "pointclick")
LOOKS = ("mouse", "rmb_drag", "facing", "none")
_LOOK_OF = {"fps3d": "mouse", "tps3d": "mouse", "side2d": "facing", "topdown": "facing", "pointclick": "none"}
_VIEW_KEYS = {"side2d": {"RIGHT": "D", "LEFT": "A"}, "topdown": {"UP": "W", "DOWN": "S", "LEFT": "A", "RIGHT": "D"}}


@dataclass(frozen=True)
class Profile:
    id: str
    name: str = "the game"
    view: str = "tps3d"
    look: str = "mouse"
    keys: dict[str, str] = field(default_factory=dict)
    hfov: float = 80.0

    @property
    def walks(self) -> bool:
        """The player moves with keys (every view but point and click)."""
        return self.view != "pointclick"

    @property
    def turns_camera(self) -> bool:
        return self.look in ("mouse", "rmb_drag")

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "view": self.view, "look": self.look, "keys": dict(self.keys),
                "hfov": self.hfov}


BUILTIN: dict[str, Profile] = {
    "fortnite": Profile("fortnite", "Fortnite", "tps3d", "mouse"),
    "fps": Profile("fps", "the game", "fps3d", "mouse"),
    "tps": Profile("tps", "the game", "tps3d", "mouse"),
    "roblox": Profile("roblox", "Roblox", "tps3d", "rmb_drag"),
    "side2d": Profile("side2d", "the game", "side2d", "facing", _VIEW_KEYS["side2d"]),
    "topdown": Profile("topdown", "the game", "topdown", "facing", _VIEW_KEYS["topdown"]),
    "pointclick": Profile("pointclick", "the game", "pointclick", "none"),
}


def parse_keys(raw: Any) -> dict[str, str]:
    """'SPACE=UP, E=F' or {"SPACE": "UP"} -> {"SPACE": "UP", "E": "F"}; unknown keys raise ValueError."""
    from .inputs import key_names

    if isinstance(raw, dict):
        pairs = list(raw.items())
    else:
        pairs = [part.split("=", 1) for part in str(raw or "").replace(";", ",").split(",") if "=" in part]
    out: dict[str, str] = {}
    for left, right in pairs:
        name, to = str(left).strip().upper(), key_names(str(right))
        if not name or len(to) != 1:
            raise ValueError(f"Key map {left}={right}: give one key on each side, like SPACE=UP")
        out[name] = to[0]
    return out


def make(base: str = "", *, name: str = "", view: str = "", keys: Any = None) -> Profile:
    """A built-in profile by id (or view), with the game's name, view and keys swapped in."""
    base = (base or "").strip().lower()
    view = (view or "").strip().lower()
    if base and base not in BUILTIN:
        raise ValueError(f"Unknown profile {base!r}: use one of {', '.join(BUILTIN)}")
    if view and view not in VIEWS:
        raise ValueError(f"Unknown view {view!r}: use one of {', '.join(VIEWS)}")
    prof = BUILTIN.get(base) or BUILTIN["tps"]
    if view and view != prof.view:
        prof = replace(prof, view=view, look=_LOOK_OF[view], keys={**_VIEW_KEYS.get(view, {}), **prof.keys})
    if name:
        prof = replace(prof, name=name.strip()[:60])
    if keys:
        prof = replace(prof, keys={**prof.keys, **parse_keys(keys)})
    return prof


def from_dict(raw: Any) -> Profile:
    """A saved profile back (unknown or broken fields fall back to the built-in one)."""
    raw = raw if isinstance(raw, dict) else {}
    try:
        prof = make(str(raw.get("id") or ""), name=str(raw.get("name") or ""), view=str(raw.get("view") or ""))
    except ValueError:
        prof = BUILTIN["tps"]
    keys = raw.get("keys") if isinstance(raw.get("keys"), dict) else {}
    look = str(raw.get("look") or "")
    try:
        hfov = float(raw.get("hfov") or prof.hfov)
    except (TypeError, ValueError):
        hfov = prof.hfov
    return replace(prof, keys={**prof.keys, **{str(k).upper(): str(v).upper() for k, v in keys.items()}},
                   look=look if look in LOOKS else _LOOK_OF.get(prof.view, prof.look), hfov=min(150.0, max(20.0, hfov)))
