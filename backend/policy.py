"""Plain-code moves from Jev's view of one frame: no language model in the loop.

``decide(view, memory, goal)`` returns the step's actions (turn, keys, click, wait), a
reason a person can read, and ``finish`` when the run is over. ``memory`` carries the
few counters that span steps (stuck, scanning, success streak). Every number that
shapes a move is in ``DEFAULTS`` so the tab can show why it moved.
"""

from __future__ import annotations

from typing import Any

DEFAULTS: dict[str, float] = {
    "confidence": 0.6,     # Jev answers below this are treated as unsure
    "done_bar": 0.75,      # the success check must pass this, twice in a row
    "done_needed": 2,
    "hfov": 80.0,          # Fortnite's horizontal field of view, degrees
    "turn_gain": 0.85,     # turn a little less than the full offset; the next frame corrects
    "center_tol": 0.08,    # box center within 8% of the middle counts as ahead
    "near_height": 0.42,   # a box this tall means the player is at the target
    "interact_hold": 0.7,  # seconds E is held to use something
    "scan_deg": 60.0,
    "scan_turns": 6,       # 6 x 60 = a full look around
    "max_loading": 20,
    "max_uses": 4,
}
_WHERE_X = {"left": 0.2, "center": 0.5, "right": 0.8}


def new_memory() -> dict[str, Any]:
    return {"done": 0, "still": 0, "scan": 0, "loading": 0, "uses": 0, "nudges": 0,
            "lost": 0, "last_cx": None, "last": "", "side": 1, "settle": 0, "scan_dir": 1}


def settings(overrides: dict[str, Any] | None = None) -> dict[str, float]:
    out = dict(DEFAULTS)
    for key, value in (overrides or {}).items():
        if key in out:
            try:
                out[key] = float(value)
            except (TypeError, ValueError):
                continue
    return out


def _side(dx: float) -> str:
    return "right" if dx > 0 else "left"


def _deg(value: float) -> str:
    return f"{abs(value):g}°"


def _explore(view: dict[str, Any], m: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    path = view.get("path") or {}
    way = path.get("choice") if float(path.get("p") or 0) >= 0.4 else "ahead"
    if way == "left":
        m["last"] = "walk"
        return [{"look": -50.0}, {"keys": ["W"], "seconds": 0.8}], "ahead is blocked, so turn left 50° and walk"
    if way == "right":
        m["last"] = "walk"
        return [{"look": 50.0}, {"keys": ["W"], "seconds": 0.8}], "ahead is blocked, so turn right 50° and walk"
    if way == "blocked":
        m["last"] = "turn"
        return [{"look": 120.0}], "blocked ahead, left and right: turn around 120°"
    m["last"] = "walk"
    return [{"keys": ["W"], "seconds": 1.2}], "walk ahead 1.2 s to look somewhere else"


def decide(view: dict[str, Any], memory: dict[str, Any] | None, goal: dict[str, Any]) -> dict[str, Any]:
    m = dict(memory or new_memory())
    cfg = settings(goal.get("settings"))
    conf = cfg["confidence"]
    target = str(goal.get("target") or "").strip()
    success = str(goal.get("success") or "").strip()
    interact = bool(goal.get("interact"))

    def out(actions: list[dict[str, Any]], why: str, finish: dict[str, str] | None = None, aim: dict[str, Any] | None = None,
            rule: str = "") -> dict[str, Any]:
        return {"actions": actions, "why": why, "finish": finish, "memory": m, "aim": aim, "rule": rule}

    # 1. Is the game even on screen?
    screen = view.get("screen") or {}
    shown = str(screen.get("choice") or "playing")
    if shown != "playing" and float(screen.get("p") or 0) >= conf:
        if shown == "loading":
            m["loading"] += 1
            if m["loading"] > cfg["max_loading"]:
                return out([], "a loading screen for too long", {"status": "failed", "reason": "Stuck on a loading screen."}, rule="loading")
            return out([{"wait": 1.0}], f"loading screen: wait 1 s ({m['loading']})", rule="loading")
        if shown == "eliminated":
            return out([], "the player was eliminated", {"status": "failed", "reason": "The player was eliminated."}, rule="eliminated")
        return out([], f"a {shown} covers the game", {"status": "blocked", "reason": f"A {shown} covers the game: close it, then run the test again."}, rule="blocked")
    m["loading"] = 0

    # 2. Did the success check pass (twice, so one lucky frame is not enough)?
    done = view.get("done")
    if success and done is not None:
        if done >= max(conf, cfg["done_bar"]):
            m["done"] += 1
            if m["done"] >= cfg["done_needed"]:
                return out([], f"success check passed {m['done']} times ({done:.0%})", {"status": "success", "reason": f"Success check passed: {success}"}, rule="done")
            return out([{"wait": 0.3}], f"success check passed once ({done:.0%}): look again to be sure", rule="done-once")
        m["done"] = 0

    # 3. Stuck? Two walks in a row and the player did not move (the beacon's real
    # movement when it reports, else Jev comparing the last two frames).
    moved = view.get("moved")
    if view.get("moved_m") is not None:
        moved = 1.0 if float(view["moved_m"]) >= 0.25 else 0.0
    if moved is not None:
        if m["last"] == "walk" and moved < 0.35:
            m["still"] += 1
        elif moved >= 0.35:
            m["still"] = 0
    if m["still"] >= 2:
        m["still"] = 0
        side = 1 if m["side"] >= 0 else -1
        m["side"] = -side
        m["last"] = "unstick"
        strafe = "D" if side > 0 else "A"
        return out(
            [{"keys": ["SPACE"], "seconds": 0.1}, {"keys": ["W", strafe], "seconds": 0.6}, {"look": 45.0 * side}],
            f"stuck: the view did not change after 2 walks ({moved:.0%}), so jump, step {_side(side)} and turn 45°",
            rule="unstick",
        )

    geo = view.get("geo")
    if not target and not geo:
        return _free(view, m, goal, cfg, out)
    name = target or "the target"

    # 4. Head for the target.
    seen = view.get("seen")
    where = view.get("where") or {}
    if not target:
        visible = False
    elif seen is not None:
        visible = seen >= conf
    else:
        visible = bool(where) and where.get("choice") != "none" and float(where.get("p") or 0) >= conf
    if geo and not (visible and geo["floor"] == "same" and geo["distance_m"] <= 15.0):
        # Jev can't see it, or it is far or on another floor: steer by coordinates.
        return _by_coordinates(view, m, goal, cfg, out, geo, name)
    if not visible:
        m["lost"] += 1
        if m["last_cx"] is not None and m["lost"] <= 2:
            back = 30.0 if m["last_cx"] >= 0.5 else -30.0
            m["last"] = "turn"
            return out([{"look": back}], f"lost sight of {target}: turn back {_side(back)} 30°", rule="lost")
        m["last_cx"] = None
        if m["scan"] < cfg["scan_turns"]:
            m["scan"] += 1
            m["last"] = "turn"
            turn = cfg["scan_deg"] * (1 if m.get("scan_dir", 1) >= 0 else -1)
            return out([{"look": turn}], f"looking for {target}: turn {_side(turn)} {cfg['scan_deg']:g}° ({m['scan']}/{cfg['scan_turns']:g})", rule="scan")
        m["scan"] = 0
        acts, how = _explore(view, m)
        return out(acts, f"{target} not found after a full look around: {how}", rule="explore")

    m["lost"] = 0
    m["scan"] = 0
    box = view.get("box")
    near = view.get("near")
    if box:
        cx = (box["left"] + box["right"]) / 2.0
        h = box["bottom"] - box["top"]
        close = h >= cfg["near_height"] or (box["bottom"] >= 0.97 and h >= 0.3)
    else:
        cx = _WHERE_X.get(str(where.get("choice")), 0.5)
        h = 0.1 + 0.4 * float(near) if near is not None else 0.2
        close = near is not None and near >= 0.75
    if geo and geo.get("arrived"):
        close = True  # the coordinates say we are there, whatever size the box looks
    m["last_cx"] = cx
    off = cx - 0.5
    turn = round(off * cfg["hfov"] * cfg["turn_gain"], 1)
    aim = {"cx": round(cx, 3), "size": round(h, 3), "turn": turn if abs(off) > cfg["center_tol"] else 0.0}
    acts: list[dict[str, Any]] = []
    said: list[str] = []
    if abs(off) > cfg["center_tol"]:
        acts.append({"look": turn})
        said.append(f"turn {_side(turn)} {_deg(turn)}")
    place = f"{_side(off)} of center" if abs(off) > cfg["center_tol"] else "straight ahead"

    if not close:
        walk = round(min(1.5, max(0.3, 0.35 + (cfg["near_height"] - h) * 2.4)), 2)
        acts.append({"keys": ["W"], "seconds": walk})
        said.append(f"walk {walk:g} s")
        m["last"] = "walk"
        return out(acts, f"{target} is {place} and still far (size {h:.2f}): " + ", then ".join(said), aim=aim, rule="approach")

    return _at_target(view, m, goal, cfg, out, acts, aim, name)


def _at_target(view: dict[str, Any], m: dict[str, Any], goal: dict[str, Any], cfg: dict[str, float], out: Any,
               acts: list[dict[str, Any]], aim: dict[str, Any] | None, name: str) -> dict[str, Any]:
    """At the target: use it (E) when asked, wait for the success check, or finish."""
    conf = cfg["confidence"]
    success = str(goal.get("success") or "").strip()
    if goal.get("interact"):
        prompt = view.get("prompt")
        if success and int(m.get("settle") or 0) > 0:
            # Just used it: give the game a moment to answer before using it again.
            m["settle"] = int(m["settle"]) - 1
            m["last"] = "wait"
            acts.append({"wait": 0.6})
            return out(acts, f"used {name}: waiting for the success check ({2 - m['settle']}/2)", aim=aim, rule="settle")
        if (prompt is not None and prompt >= conf) or m["nudges"] >= 3:
            hold = cfg["interact_hold"]
            acts.append({"keys": ["E"], "seconds": hold})
            m["uses"] += 1
            m["nudges"] = 0
            m["last"] = "use"
            reason = "the use prompt is up" if prompt is not None and prompt >= conf else "no prompt after 3 nudges"
            why = f"at {name} and {reason}: hold E {hold:g} s"
            if not success:
                return out(acts, why, {"status": "success", "reason": f"Reached {name} and used it (E)."}, aim=aim, rule="use")
            if m["uses"] > cfg["max_uses"]:
                return out([], why, {"status": "failed", "reason": f"Used {name} {m['uses'] - 1} times but the success check never passed."}, aim=aim, rule="use")
            m["settle"] = 2
            return out(acts, why, aim=aim, rule="use")
        m["nudges"] += 1
        m["last"] = "walk"
        acts.append({"keys": ["W"], "seconds": 0.25, "timed": True})
        return out(acts, f"next to {name}, no use prompt yet: step closer ({m['nudges']}/3)", aim=aim, rule="nudge")

    if not success:
        return out(acts, f"reached {name}", {"status": "success", "reason": f"Reached {name}."}, aim=aim, rule="reached")
    m["last"] = "wait"
    acts.append({"wait": 0.4})
    return out(acts, f"at {name}: waiting for the success check", aim=aim, rule="wait-success")


def _by_coordinates(view: dict[str, Any], m: dict[str, Any], goal: dict[str, Any], cfg: dict[str, float], out: Any,
                    geo: dict[str, Any], name: str) -> dict[str, Any]:
    """Steer by where the beacon says the player is and where the level says the target is."""
    from .geo import describe

    conf = cfg["confidence"]
    turn = float(geo["turn_deg"])
    aim = {"turn": round(turn, 1), "distance_m": geo["distance_m"], "height_m": geo["height_m"]}
    where = describe(geo)
    if geo.get("arrived"):
        return _at_target(view, m, goal, cfg, out, [], aim, name)

    # Another floor and close enough that the way is up or down, not across.
    if geo["floor"] != "same" and geo["distance_m"] <= max(8.0, 3.0 * abs(geo["height_m"])):
        way = "up" if geo["floor"] == "up" else "down"
        climb = view.get("climb_m")
        if climb is not None and ((way == "up" and climb > 0.3) or (way == "down" and climb < -0.3)):
            m["last"] = "walk"
            return out([{"keys": ["W"], "seconds": 0.9}],
                       f"{name} is {where}: already going {way} ({climb:+.1f} m since the last step), keep walking",
                       aim=aim, rule="floor-climb")
        stairs = view.get("stairs_seen")
        sbox = view.get("stairs_box")
        if stairs is not None and stairs >= conf:
            m["scan"] = 0
            acts: list[dict[str, Any]] = []
            said = []
            if sbox:
                off = (sbox["left"] + sbox["right"]) / 2.0 - 0.5
                if abs(off) > cfg["center_tol"]:
                    look = round(off * cfg["hfov"] * cfg["turn_gain"], 1)
                    acts.append({"look": look})
                    said.append(f"turn {_side(look)} {_deg(look)}")
            acts.append({"keys": ["W"], "seconds": 0.9})
            said.append("walk 0.9 s")
            m["last"] = "walk"
            return out(acts, f"{name} is {where}: head for the way {way} ({stairs:.0%}): " + ", then ".join(said), aim=aim, rule="floor-stairs")
        if m["scan"] < cfg["scan_turns"]:
            m["scan"] += 1
            m["last"] = "turn"
            return out([{"look": cfg["scan_deg"]}], f"{name} is {where}: looking for stairs, a ramp or a ladder {way} ({m['scan']}/{cfg['scan_turns']:g})", aim=aim, rule="floor-scan")
        m["scan"] = 0
        acts, how = _explore(view, m)
        return out(acts, f"no way {way} in sight: {how}", aim=aim, rule="floor-explore")

    m["scan"] = 0
    # Behind or well to the side: turn first, no walking blind.
    if abs(turn) > 60.0:
        look = max(-120.0, min(120.0, turn))
        m["last"] = "turn"
        return out([{"look": round(look, 1)}], f"{name} is {where}: turn {_side(look)} {_deg(round(look, 1))} first", aim=aim, rule="coord-turn")

    acts = []
    said = []
    if abs(turn) > 12.0:
        acts.append({"look": round(turn, 1)})
        said.append(f"turn {_side(turn)} {_deg(round(turn, 1))}")
    path = view.get("path") or {}
    way = path.get("choice")
    if way in ("left", "right", "blocked") and float(path.get("p") or 0) >= conf:
        # Facing it but a wall or building is in the way: go around it.
        side = -1.0 if way == "left" else 1.0 if way == "right" else (1.0 if m["side"] >= 0 else -1.0)
        if way == "blocked":
            m["side"] = -m["side"]
        acts.append({"look": 50.0 * side})
        acts.append({"keys": ["W"], "seconds": 0.9})
        m["last"] = "walk"
        return out(acts, f"{name} is {where}, but a wall or building is in the way: go around on the {_side(side)}", aim=aim, rule="coord-detour")
    walk = round(min(1.5, max(0.4, 0.4 + geo["distance_m"] * 0.04)), 2)
    acts.append({"keys": ["W"], "seconds": walk})
    said.append(f"walk {walk:g} s")
    m["last"] = "walk"
    return out(acts, f"{name} is {where}: " + ", then ".join(said), aim=aim, rule="coord-walk")


_NEXT = {
    "forward": ([{"keys": ["W"], "seconds": 0.9}], "walk forward 0.9 s"),
    "turn_left": ([{"look": -45.0}], "turn left 45°"),
    "turn_right": ([{"look": 45.0}], "turn right 45°"),
    "back": ([{"keys": ["S"], "seconds": 0.6}], "walk back 0.6 s"),
    "jump": ([{"keys": ["W", "SPACE"], "seconds": 0.5}], "jump forward"),
    "interact": ([{"keys": ["E"], "seconds": 0.7}], "hold E 0.7 s"),
}


def _free(view: dict[str, Any], m: dict[str, Any], goal: dict[str, Any], cfg: dict[str, float], out: Any) -> dict[str, Any]:
    """No target named: Jev picks the move from the goal, the walkable path keeps it out of walls."""
    nxt = view.get("next") or {}
    pick = str(nxt.get("choice") or "")
    p = float(nxt.get("p") or 0)
    path = view.get("path") or {}
    if pick in _NEXT and p >= 0.4:
        if pick == "forward" and path.get("choice") in ("left", "right", "blocked") and float(path.get("p") or 0) >= cfg["confidence"]:
            acts, how = _explore(view, m)
            return out(acts, f"Jev picked forward ({p:.0%}) but {how}", rule="free-explore")
        acts, how = _NEXT[pick]
        m["last"] = "walk" if pick in ("forward", "back", "jump") else ("use" if pick == "interact" else "turn")
        return out([dict(a) for a in acts], f"Jev picked {pick.replace('_', ' ')} ({p:.0%}): {how}", rule="jev-pick")
    acts, how = _explore(view, m)
    return out(acts, f"no clear move from Jev: {how}", rule="free-explore")
