"""The questions Jev answers about every frame, all in one request.

TypeSafe's advice is to batch every question about the same state and to keep each one
as simple as possible (models do worse on images than on text). The frame is
``frame`` in the state and the frame before the last move is ``previous``.
"""

from __future__ import annotations

from typing import Any

SCREEN = {
    "playing": "The island in play: the player's character or first-person view can be controlled; no menu, map, inventory, loading screen or dialog covers the view.",
    "menu": "A menu, map, inventory, settings or pause screen covers most of the view.",
    "loading": "A loading screen, a black or nearly black screen, or a spinner.",
    "eliminated": "The player is eliminated, spectating or waiting to respawn.",
    "popup": "A dialog, popup or message box in the middle of the screen waits for a button press.",
}
WHERE = {
    "left": "In the left third of the image.",
    "center": "In the middle third of the image.",
    "right": "In the right third of the image.",
    "none": "Not in the image.",
}
NEAR = [
    "far away: small in the image",
    "a few steps away",
    "close enough to touch or use",
]
PATH = {
    "ahead": "Straight ahead is open ground.",
    "left": "Straight ahead is blocked but the left side is open.",
    "right": "Straight ahead is blocked but the right side is open.",
    "blocked": "Ahead, left and right all look blocked.",
}
NEXT = {
    "forward": "Walk forward.",
    "turn_left": "Turn the camera left.",
    "turn_right": "Turn the camera right.",
    "back": "Walk backward.",
    "jump": "Jump (over a small step or gap).",
    "interact": "Use or interact with what is right in front of the player.",
}


def step_questions(
    *,
    goal: str,
    target: str = "",
    success: str = "",
    interact: bool = False,
    has_previous: bool = False,
    box_ok: bool = True,
    floor: str = "",
) -> dict[str, Any]:
    q: dict[str, Any] = {
        "screen": {"type": "choice", "instructions": "What does `frame` show? It is a screenshot of Fortnite.", "criteria": SCREEN},
        "path": {
            "type": "choice",
            "instructions": "Seen from the player's view in `frame`, which way can the player walk a few steps without walking into a wall, fence, prop or a drop?",
            "criteria": PATH,
        },
    }
    if success:
        q["done"] = {"type": "noul", "instructions": f"Look at `frame`. {success.strip()}"}
    if target:
        t = target.strip()
        q["seen"] = {"type": "noul", "instructions": f"Is {t} visible in `frame`?"}
        q["where"] = {"type": "choice", "instructions": f"Where is {t} in `frame`?", "criteria": WHERE}
        if box_ok:
            q["target"] = {"type": "box", "instructions": f"{t} in `frame`"}
        else:
            q["near"] = {"type": "score", "instructions": f"How close is the player to {t} in `frame`?", "criteria": NEAR}
    else:
        q["next"] = {
            "type": "choice",
            "instructions": f"The player's goal is: {goal.strip()}. Looking at `frame`, which single move helps most right now?",
            "criteria": NEXT,
        }
    if has_previous:
        q["moved"] = {
            "type": "noul",
            "instructions": "Compare `previous` (before the last move) with `frame` (now): did the view change, meaning the player walked or turned?",
        }
    if floor in ("up", "down"):
        way = "up to a higher floor" if floor == "up" else "down to a lower floor"
        q["stairs_seen"] = {
            "type": "noul",
            "instructions": f"Does `frame` show stairs, a ramp, a ladder, a jump pad or an elevator that leads {way}?",
        }
        if box_ok:
            q["stairs"] = {"type": "box", "instructions": f"the stairs, ramp, ladder or jump pad leading {way} in `frame`"}
    if interact:
        q["prompt"] = {
            "type": "noul",
            "instructions": "Does `frame` show an interaction prompt: a key or button hint to use, open, pick up, press or talk, such as E with a label?",
        }
    return q


def read_view(answers: dict[str, Any]) -> dict[str, Any]:
    """Jev's answers as the flat view the decision rules read."""
    from . import jev

    view: dict[str, Any] = {}
    for key in ("screen", "where", "path", "next"):
        hit = jev.choice(answers, key)
        if hit:
            view[key] = {"choice": hit[0], "p": round(hit[1], 3), "all": jev.probabilities(answers, key)}
    for key in ("done", "seen", "moved", "prompt", "stairs_seen"):
        value = jev.noul(answers, key)
        if value is not None:
            view[key] = round(value, 3)
    near = jev.score(answers, "near")
    if near is not None:
        view["near"] = round(near, 3)
    for key, name in (("target", "box"), ("stairs", "stairs_box")):
        edges = jev.box(answers, key)
        if edges:
            view[name] = {k: round(v, 4) for k, v in edges.items()}
    return view
