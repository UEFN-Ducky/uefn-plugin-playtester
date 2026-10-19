"""What runs teach the next run.

Every finished run leaves a compact **lesson** (``lesson.<run id>``, kept for the last
``KEEP_LESSONS`` runs, long after its frames and full record are pruned). Lessons for
the same island and target fold into a **target memory** (``memory.<island>.<target>``):
where it was found and reached, which side it first showed up on, tries, wins and what
blocked it. The next run of that target is **seeded** from the memory: remembered
coordinates when the beacon reports and no level actor is given, the first scan toward
the side it was found last, and a warning when menus or popups stopped recent runs.
"""

from __future__ import annotations

import re
import time
from typing import Any

KEEP_LESSONS = 500
KEEP_RUN_IDS = 10
_ARTICLE = re.compile(r"^(the|a|an)\s+", re.I)
_KEY = re.compile(r"[^a-z0-9]+")
_TURN_RULES = ("scan", "lost", "coord-turn", "floor-scan")


def key(text: Any, size: int = 48) -> str:
    """'The Vending Machine!' -> 'vending-machine' (doc-key safe)."""
    t = _ARTICLE.sub("", str(text or "").strip().lower())
    return _KEY.sub("-", t).strip("-")[:size] or ""


def memory_key(island: str, target: str) -> str:
    return f"memory.{key(island, 40) or 'island'}.{key(target)}"


# ---------------------------------------------------------------- per-step bookkeeping


def tally(run: dict[str, Any], step: dict[str, Any], conf: float) -> None:
    """Fold one step into the run's totals (cheap; called for every step)."""
    t = run.setdefault("totals", {"turns": 0, "turn_deg": 0.0, "walked_m": 0.0, "stuck": 0, "uses": 0,
                                   "first_seen": None, "false_boxes": 0, "seen_sum": 0.0, "seen_n": 0,
                                   "cut_short": 0, "jev_errors": 0, "rules": {}})
    rule = step.get("rule") or ("jev-error" if step.get("error") else "")
    if rule:
        t["rules"][rule] = t["rules"].get(rule, 0) + 1
    if step.get("error"):
        t["jev_errors"] += 1
    for act in step.get("done") or []:
        look = act.get("look") if isinstance(act, dict) else None
        if isinstance(look, dict) and look.get("dx_deg"):
            t["turns"] += 1
            t["turn_deg"] = round(t["turn_deg"] + abs(float(look["dx_deg"])), 1)
        for part in (act.values() if isinstance(act, dict) else []):
            if isinstance(part, dict) and part.get("cut_short"):
                t["cut_short"] += 1
    if rule == "unstick":
        t["stuck"] += 1
    if rule == "use":
        t["uses"] += 1
    view = step.get("view") or {}
    seen = view.get("seen")
    if seen is not None:
        t["seen_sum"] = round(t["seen_sum"] + float(seen), 3)
        t["seen_n"] += 1
        if float(seen) >= conf and t["first_seen"] is None:
            t["first_seen"] = step.get("n")
            run["found"] = {"step": step.get("n"), "side": side_of(view),
                            "turned_deg": t["turn_deg"], "pose": (step.get("where") or {}).get("pose")}
        if view.get("box") and float(seen) < conf:
            t["false_boxes"] += 1
    moved = (step.get("where") or {}).get("moved_m")
    if moved:
        t["walked_m"] = round(t["walked_m"] + float(moved), 2)


def side_of(view: dict[str, Any]) -> str:
    box = view.get("box")
    if box:
        cx = (float(box["left"]) + float(box["right"])) / 2.0
        return "left" if cx < 0.42 else "right" if cx > 0.58 else "center"
    where = view.get("where") or {}
    return str(where.get("choice") or "")


# ---------------------------------------------------------------- lessons


def lesson(run: dict[str, Any]) -> dict[str, Any]:
    """The compact record a finished run leaves behind."""
    t = dict(run.get("totals") or {})
    seen_n = int(t.pop("seen_n", 0) or 0)
    seen_sum = float(t.pop("seen_sum", 0.0) or 0.0)
    t["seen_avg"] = round(seen_sum / seen_n, 3) if seen_n else None
    steps = run.get("steps") or []
    reached = None
    if run.get("status") == "success":
        for s in reversed(steps):
            pose = (s.get("where") or {}).get("pose")
            if pose:
                reached = {k: pose[k] for k in ("x", "y", "z", "yaw") if k in pose}
                break
    focus = {}
    for ev in run.get("events") or []:
        if ev.get("kind") == "focus":
            name = str(ev.get("method") or "?")
            focus[name] = focus.get(name, 0) + 1
    return {
        "id": run.get("id"), "island": run.get("island") or "", "goal": run.get("goal"), "target": run.get("target"),
        "target_key": key(run.get("target") or run.get("target_actor")), "target_actor": run.get("target_actor") or "",
        "outcome": run.get("status"), "reason": run.get("reason"), "dry_run": bool(run.get("dry_run")),
        "steps": run.get("n_steps"), "seconds": run.get("seconds"), "avg_ms": run.get("avg_ms"), "jev_ms": run.get("jev_ms"),
        "model": run.get("model"), "totals": t, "found": run.get("found"), "reached": reached,
        "target_point": run.get("target_point"), "counts_per_90": (run.get("settings") or {}).get("counts"),
        "focus": focus, "memory_used": run.get("memory_used"), "ended": run.get("ended") or time.time(),
    }


def blocker_of(outcome: str, reason: str) -> str:
    r = (reason or "").lower()
    if outcome == "blocked":
        for word in ("menu", "popup", "loading"):
            if word in r:
                return word
        return "blocked"
    if outcome == "failed":
        if "eliminated" in r:
            return "eliminated"
        if "jev" in r or "typesafe" in r:
            return "jev"
        if "front" in r or "focus" in r:
            return "focus"
        return "failed"
    if outcome == "timeout":
        return "timeout"
    return ""


def fold(memory: dict[str, Any] | None, les: dict[str, Any]) -> dict[str, Any]:
    """A target memory updated with one more lesson."""
    m = dict(memory or {"island": les.get("island"), "target": les.get("target") or les.get("target_actor"),
                        "tries": 0, "wins": 0, "sides": {}, "blockers": {}, "runs": [], "win_steps": []})
    m["tries"] = int(m.get("tries") or 0) + 1
    if les.get("outcome") == "success" and not les.get("dry_run"):
        m["wins"] = int(m.get("wins") or 0) + 1
        m["win_steps"] = (list(m.get("win_steps") or []) + [les.get("steps")])[-10:]
    found = les.get("found") or {}
    if found.get("side"):
        sides = dict(m.get("sides") or {})
        sides[found["side"]] = sides.get(found["side"], 0) + 1
        m["sides"] = sides
        m["last_side"] = found["side"]
        if found.get("pose"):
            m["seen_from"] = found["pose"]
    if les.get("reached"):
        m["reached"] = les["reached"]
    if les.get("target_point"):
        m["point"] = les["target_point"]
    if les.get("target_actor"):
        m["target_actor"] = les["target_actor"]
    b = blocker_of(str(les.get("outcome") or ""), str(les.get("reason") or ""))
    if b:
        blockers = dict(m.get("blockers") or {})
        blockers[b] = blockers.get(b, 0) + 1
        m["blockers"] = blockers
    m["last_outcome"] = les.get("outcome")
    m["last_reason"] = les.get("reason")
    m["runs"] = (list(m.get("runs") or []) + [les.get("id")])[-KEEP_RUN_IDS:]
    m["updated"] = les.get("ended") or time.time()
    return m


def seed(memory: dict[str, Any] | None, *, beacon_on: bool, has_point: bool) -> dict[str, Any]:
    """What the next run takes from memory: {point?, scan_dir, warn?, source} (empty when nothing)."""
    if not memory or not int(memory.get("tries") or 0):
        return {}
    out: dict[str, Any] = {"source": (memory.get("runs") or [""])[-1], "tries": memory.get("tries"),
                           "wins": memory.get("wins")}
    if beacon_on and not has_point:
        point = memory.get("point") or memory.get("reached")
        if point:
            out["point"] = {k: float(point[k]) for k in ("x", "y", "z")}
            out["point_from"] = "level actor" if memory.get("point") else "where it was reached"
    side = memory.get("last_side")
    if side in ("left", "right"):
        out["scan_dir"] = -1 if side == "left" else 1
        out["scan_side"] = side
    if memory.get("last_outcome") == "blocked":
        out["warn"] = f"The last run on this target was blocked ({memory.get('last_reason') or 'a menu or popup'}): make sure Fortnite shows the game."
    return out


def summary(memory: dict[str, Any] | None, lessons: list[dict[str, Any]]) -> dict[str, Any]:
    """What agents get from playtester_lessons for one target."""
    m = memory or {}
    tries = int(m.get("tries") or len(lessons) or 0)
    wins = int(m.get("wins") or 0)
    win_steps = [s for s in (m.get("win_steps") or []) if s]
    tips: list[str] = []
    if m.get("last_side"):
        tips.append(f"It was first seen on the {m['last_side']} of the view last time.")
    if m.get("reached"):
        r = m["reached"]
        tips.append(f"Reached at x {r.get('x', 0):.0f}, y {r.get('y', 0):.0f}, z {r.get('z', 0):.0f}: runs with the beacon head there.")
    for b, n in sorted((m.get("blockers") or {}).items(), key=lambda kv: -kv[1]):
        tips.append({"menu": f"A menu stopped {n} run(s): close menus before testing.",
                     "popup": f"A popup stopped {n} run(s).",
                     "timeout": f"{n} run(s) hit the step limit: give a target or a level actor.",
                     "jev": f"Jev failed in {n} run(s): check the TypeSafe key.",
                     "focus": f"Fortnite could not come to the front in {n} run(s).",
                     "eliminated": f"The player was eliminated in {n} run(s)."}.get(b, f"{b}: {n}"))
    return {
        "target": m.get("target") or (lessons[0].get("target") if lessons else ""), "island": m.get("island", ""),
        "runs": tries, "wins": wins, "success_rate": round(wins / tries, 2) if tries else None,
        "avg_win_steps": round(sum(win_steps) / len(win_steps), 1) if win_steps else None,
        "blockers": m.get("blockers") or {}, "last_outcome": m.get("last_outcome"), "tips": tips,
        "recent": [{k: les.get(k) for k in ("id", "outcome", "reason", "steps", "seconds", "dry_run")} for les in lessons[:10]],
    }
