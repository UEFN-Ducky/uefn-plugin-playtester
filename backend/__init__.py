"""Play Tester: Jev plays the live Fortnite client and shows every move in its tab.

Tools, workflow nodes and the tab's panel calls all run the same functions below.
Nothing here touches the game until it is given something to do (a goal, keys, a
target): empty calls answer with what to give.
"""

from __future__ import annotations

import json
import time
from typing import Any

from . import beacon, brains, geo, jev, pilot, policy, questions, runs, screen

PLUGIN_ID = "playtester"
INTENT = (
    r"\b(play ?test(s|ing|er)?|play-?test\w*|in[- ]game|ingame|fortnite client|test (it |this |that |my |the )?"
    r"(island|map|level|game)|walk (the player|to)|move the (player|character)|jev)\b"
)

_PILOT: pilot.Pilot | None = None


def _settings() -> dict[str, Any]:
    try:
        from frontend.ui_web.plugin_host_api import prefs_plugin_get

        return prefs_plugin_get(PLUGIN_ID) or {}
    except Exception:
        return {}


def _save_setting(key: str, value: Any) -> None:
    try:
        from frontend.ui_web.plugin_host_api import prefs_plugin_get, prefs_plugin_set

        slot = dict(prefs_plugin_get(PLUGIN_ID) or {})
        slot[key] = value
        prefs_plugin_set(PLUGIN_ID, slot)
    except Exception:
        pass


def _island() -> str:
    """The open project's name (the island runs are about); '' when Ducky has none."""
    try:
        import os

        from frontend.settings import PanelSettings

        root = PanelSettings.load().uefn_project_root or ""
        return os.path.basename(os.path.normpath(root)) if root else ""
    except Exception:
        return ""


def _push(event: dict[str, Any]) -> None:
    from frontend.ui_web.verse_editor.panel_events import push_agent_event

    push_agent_event(event)


def _bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _questions_arg(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return None
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    return raw if isinstance(raw, dict) and raw else None


def _compact(answers: dict[str, Any]) -> dict[str, Any]:
    """Jev's answers without the noise: value, probabilities and confidence per question."""
    out: dict[str, Any] = {}
    for key, ans in (answers or {}).items():
        if not isinstance(ans, dict):
            continue
        kind = str(ans.get("type") or "")
        row: dict[str, Any] = {"type": kind}
        if kind == "noul":
            row["noul"] = jev.noul(answers, key)
        elif kind == "choice":
            hit = jev.choice(answers, key)
            row["choice"] = hit[0] if hit else None
            row["probabilities"] = jev.probabilities(answers, key)
        elif kind == "score":
            row["score"] = jev.score(answers, key)
        elif kind == "box":
            row["box"] = jev.box(answers, key)
        if ans.get("confidence") is not None:
            row["confidence"] = ans.get("confidence")
        out[key] = row
    return out


BEACON_HOW = (
    "1) playtester_beacon(add=true) adds Content/Verse/PlayTester/playtester_beacon_device.verse to the project. "
    "2) Build Verse and push the changes. 3) Place playtester_beacon_device anywhere in the level. "
    "4) Launch Session: its [PTPOS] lines reach the UEFN Output Log and playtester_status shows the beacon on."
)


def register(api: Any) -> None:
    global _PILOT
    store = runs.Runs(api.data, api.local_dir("frames"))
    client = brains.Brain(jev.JevClient(), _settings)
    bcn = beacon.Beacon()

    def map_key() -> str:
        from . import learning

        return f"map.{learning.key(_island(), 40) or 'island'}"

    def level_map(refresh: bool = False) -> dict[str, Any]:
        """Every device's label and position in the open island, read once from the UEFN editor and kept."""
        doc = store.data.get(map_key())
        if isinstance(doc, dict) and doc.get("actors") and not refresh:
            return {"ok": True, **doc, "cached": True}
        try:
            out = api.listener("actor_state_snapshot", {"limit": 600}, timeout=30)
        except Exception:
            if isinstance(doc, dict) and doc.get("actors"):
                return {"ok": True, **doc, "cached": True, "note": "UEFN isn't connected: this is the last map read."}
            return {"ok": False, "error": "UEFN isn't connected, so Play Tester can't read the level: open the island in UEFN."}
        if isinstance(out, dict) and "actors" not in out and isinstance(out.get("result"), dict):
            out = out["result"]
        actors = []
        for r in (out or {}).get("actors") or []:
            pt = geo.point((r or {}).get("location"))
            if pt and r.get("label"):
                actors.append({"label": str(r["label"]), "class": str(r.get("class") or ""), **{k: round(v, 1) for k, v in pt.items()}})
        doc = {"island": _island(), "at": time.time(), "count": len(actors), "actors": actors}
        try:
            store.data.put(map_key(), doc)
        except Exception:
            pass
        return {"ok": True, **doc, "cached": False}

    def landmarks() -> list[dict[str, Any]]:
        doc = store.data.get(map_key())
        rows = (doc or {}).get("actors") if isinstance(doc, dict) else None
        return [{"name": r["label"], "x": r["x"], "y": r["y"], "z": r["z"], "source": "level"} for r in rows or []]

    def from_map(label: str) -> dict[str, Any] | None:
        for r in landmarks():
            if r["name"].lower() == label.lower():
                return {"ok": True, "point": {k: float(r[k]) for k in ("x", "y", "z")}, "label": r["name"], "from": "saved map"}
        return None

    def locate(label: str) -> dict[str, Any]:
        """A level actor's position by its label, from the UEFN editor (or the saved map when it is busy)."""
        try:
            out = api.listener("actor_state_snapshot", {"label_filter": label, "limit": 20, "scope": "all"}, timeout=20)
        except Exception:
            saved = from_map(label)
            if saved:
                return saved
            return {"ok": False, "error": "UEFN isn't connected, so Play Tester can't look the actor up: open the project in UEFN or give target_xyz."}
        if isinstance(out, dict) and "actors" not in out and isinstance(out.get("result"), dict):
            out = out["result"]
        rows = [r for r in ((out or {}).get("actors") or []) if isinstance(r, dict)]
        exact = [r for r in rows if str(r.get("label") or "").lower() == label.lower()]
        row = exact[0] if exact else (rows[0] if len(rows) == 1 else None)
        if row is None:
            near = ", ".join(str(r.get("label")) for r in rows[:8])
            return {"ok": False, "error": f"No actor labelled {label!r} in the level" + (f"; close: {near}" if near else ".")}
        pt = geo.point(row.get("location"))
        if not pt:
            return {"ok": False, "error": f"{row.get('label')} has no readable location."}
        return {"ok": True, "point": pt, "label": str(row.get("label"))}

    the = pilot.Pilot(store, client, push=_push, settings=_settings, beacon=bcn, locate=locate,
                      save_setting=_save_setting, island=_island, landmarks=landmarks)
    _PILOT = the

    def beacon_state() -> dict[str, Any]:
        raw = bcn.pose()
        fresh = raw is not None and raw["age_s"] <= beacon.FRESH_S
        out: dict[str, Any] = {"seen": raw is not None, "on": fresh}
        if raw:
            out["age_s"] = raw["age_s"]
            out["pose"] = {k: round(float(raw[k]), 1) for k in ("x", "y", "z", "yaw")}
            if raw.get("state"):
                out["state"] = raw["state"]
        try:
            out["landmarks"] = len(bcn.marks())
        except Exception:
            pass
        return out

    # ---------------------------------------------------------------- status

    def status() -> dict[str, Any]:
        st = screen.client()
        cfg = the.cfg()
        brain = client.ready()
        key = bool(brain.get("ok"))
        age = st.get("loaded_for_s")
        playing = bool(st.get("ok") and st.get("gameplay_loaded") and (age or 0) >= screen.SETTLE_S)
        loading = bool(st.get("ok") and not playing)
        active = store.active()
        latest = store.latest()
        bcn_state = beacon_state()
        if not key:
            nxt = str(brain.get("error") or jev.NO_KEY)
        elif not st.get("ok"):
            nxt = str(st.get("error") or "No Fortnite client")
        elif loading:
            nxt = "Fortnite is loading the island: wait, then call playtester_status again."
        elif active:
            nxt = f"Run {active.get('run_id')} is playing: playtester_wait or playtester_stop."
        elif not bcn_state["on"]:
            nxt = ("Ready, but the beacon is off, so runs see only the image: set it up first "
                   "(playtester_beacon(add=true), build Verse, push, place playtester_beacon_device, launch again), "
                   "then call playtester_run(goal, target, success).")
        else:
            nxt = "Ready: call playtester_run(goal, target, success) to play test."
        return {
            "ok": True,
            "ready": bool(key and playing and not active),
            "client": {"open": bool(st.get("ok")), "playing": playing, "loading": loading,
                       "foreground": bool(st.get("foreground")), "loaded_for_s": age,
                       "error": None if st.get("ok") else st.get("error")},
            "jev": {"key": key, "own_key": bool(jev.api_key()), "brain": brain.get("brain") or None,
                    "choice": brain.get("choice"), "model": cfg["model"], "box": client.box_ok(cfg["model"]),
                    "gateway": ":".join(brain["gateway"]) if brain.get("brain") == "gateway" else None},
            "beacon": bcn_state,
            "settings": {k: cfg[k] for k in ("counts", "max_steps", "confidence", "size", "click_focus", "smooth")},
            "map": {"landmarks": len(landmarks())},
            "island": the.island(),
            "active_run": active.get("run_id") if active else None,
            "latest_run": latest,
            "next": nxt,
        }

    # ---------------------------------------------------------------- looking

    def check(qs: Any = None, label: str = "check") -> dict[str, Any]:
        parsed = _questions_arg(qs)
        if parsed is None:
            return {"ok": False, "error": "Give questions: {id: {type: noul|choice|score|box, instructions, criteria?}} and call the frame `frame`."}
        problem = jev.check_questions(parsed)
        if problem:
            return {"ok": False, "error": problem}
        t0 = time.perf_counter()
        ready = the._ready_client()
        if not ready.get("ok"):
            return ready
        frame = the.game.grab(ready["hwnd"], the.cfg()["size"])
        res = the._ask(frame, parsed, the.cfg()["model"])
        if not res.get("ok"):
            return res
        answers = _compact(res.get("answers") or {})
        boxes = {k: v["box"] for k, v in answers.items() if v.get("box")}
        first = next(iter(boxes.items()), None)
        shown = the.game.draw_box(frame["jpeg"], first[1] if first else None, first[0] if first else "")
        view = questions.read_view(res.get("answers") or {})
        why = f"{label}: " + ", ".join(f"{k} {_say(v)}" for k, v in answers.items())
        logged = the.manual(label, frame, view, [], [], why, t0, int(res.get("ms") or 0), jpeg=shown)
        return {"ok": True, "answers": answers, "boxes": boxes, "jev_ms": res.get("ms"),
                "ms": int((time.perf_counter() - t0) * 1000), "frame": logged["path"],
                "size": [frame["width"], frame["height"]]}

    def find(target: str = "") -> dict[str, Any]:
        t = (target or "").strip()
        if not t:
            return {"ok": False, "error": "Give a target to find, for example: the red button."}
        box_ok = client.box_ok(the.cfg()["model"])
        qs: dict[str, Any] = {
            "seen": {"type": "noul", "instructions": f"Is {t} visible in `frame`?"},
            "where": {"type": "choice", "instructions": f"Where is {t} in `frame`?", "criteria": questions.WHERE},
        }
        if box_ok:
            qs["target"] = {"type": "box", "instructions": f"{t} in `frame`"}
        out = check(qs, label=f"find {t}")
        if not out.get("ok"):
            return out
        ans = out["answers"]
        seen = (ans.get("seen") or {}).get("noul")
        box = (ans.get("target") or {}).get("box")
        found = seen is not None and seen >= the.cfg()["confidence"]
        result = {"ok": True, "target": t, "found": found, "seen": seen,
                  "where": (ans.get("where") or {}).get("choice"),
                  "box": box if found else None, "frame": out["frame"], "jev_ms": out["jev_ms"], "ms": out["ms"]}
        if box and not found:
            result["note"] = "Jev always returns a box; it is ignored because the target is not visible."
        return result

    # ---------------------------------------------------------------- pressing

    def act(kind: str, actions: list[dict[str, Any]], why: str) -> dict[str, Any]:
        active = store.active()
        if active:
            return {"ok": False, "error": f"Run {active.get('run_id')} is playing: stop it before pressing keys by hand."}
        t0 = time.perf_counter()
        ready = the._ready_client()
        if not ready.get("ok"):
            return ready
        import threading

        ctrl = the.game.controller(ready["hwnd"], threading.Event(), the.cfg()["counts"])
        try:
            done = ctrl.run(actions)
        except (ValueError, RuntimeError) as exc:
            return {"ok": False, "error": str(exc)}
        finally:
            ctrl.release_all()
        time.sleep(0.15)
        frame = the.game.grab(ready["hwnd"], the.cfg()["size"])
        logged = the.manual(kind, frame, {}, actions, done, why, t0)
        return {"ok": True, "done": done, "frame": logged["path"], "ms": int((time.perf_counter() - t0) * 1000)}

    def press(keys: Any = "", seconds: float = 0.5) -> dict[str, Any]:
        from . import inputs

        try:
            names = inputs.key_names(keys)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if not names:
            return {"ok": False, "error": "Give keys to press, for example W, W+A, SPACE or E."}
        secs = min(inputs.MAX_HOLD_S, max(inputs.MIN_HOLD_S, _num(seconds, 0.5)))
        return act("press", [{"keys": names, "seconds": secs}], f"press {'+'.join(names)} {secs:g} s")

    def look(dx_degrees: float = 0.0, dy_degrees: float = 0.0) -> dict[str, Any]:
        dx, dy = _num(dx_degrees), _num(dy_degrees)
        if not dx and not dy:
            return {"ok": False, "error": "Give dx_degrees (right is positive) or dy_degrees (down is positive)."}
        dx, dy = max(-360.0, min(360.0, dx)), max(-90.0, min(90.0, dy))
        return act("look", [{"look": dx, "look_y": dy}], f"turn {'right' if dx >= 0 else 'left'} {abs(dx):g}°" + (f", {'down' if dy > 0 else 'up'} {abs(dy):g}°" if dy else ""))

    def click(button: str = "", seconds: float = 0.05) -> dict[str, Any]:
        name = (button or "").strip().lower()
        if name not in ("left", "right"):
            return {"ok": False, "error": "Give button: left (fire / use) or right (aim)."}
        secs = min(3.0, max(0.02, _num(seconds, 0.05)))
        return act("click", [{"click": name, "seconds": secs}], f"{name} click {secs:g} s")

    def calibrate(turn_degrees: float = 0.0) -> dict[str, Any]:
        """Turn a known amount and find the same spot again with Jev, to learn the mouse speed."""
        turn = _num(turn_degrees)
        if not 5 <= abs(turn) <= 40:
            return {"ok": False, "error": "Give turn_degrees between 5 and 40 (20 works well); the spot must stay in view."}
        if store.active():
            return {"ok": False, "error": "A run is playing: stop it first."}
        if not client.box_ok(the.cfg()["model"]):
            return {"ok": False, "error": "Calibration needs Box questions: pick jev-1.14.0 or jev-preview in Settings → Play Tester."}
        t0 = time.perf_counter()
        ready = the._ready_client()
        if not ready.get("ok"):
            return ready
        cfg = the.cfg()
        hwnd = ready["hwnd"]
        before = the.game.grab(hwnd, cfg["size"])
        img = before["image"]
        w, h = img.size
        crop = img.crop((int(w * 0.4), int(h * 0.35), int(w * 0.6), int(h * 0.65)))
        crop = screen.fit(crop, max(screen.MIN_SIDE + 20, crop.size[0] * 2))
        spot = jev.new_ref("spot")
        import threading

        ctrl = the.game.controller(hwnd, threading.Event(), cfg["counts"])
        try:
            ctrl.look(turn)
        except (ValueError, RuntimeError) as exc:
            ctrl.release_all()
            return {"ok": False, "error": str(exc)}
        time.sleep(0.35)
        after = the.game.grab(hwnd, cfg["size"])
        qs = {
            "same": {"type": "noul", "instructions": f"Does `frame` still show the place in this picture: {spot}?"},
            "spot": {"type": "box", "instructions": f"the part of `frame` that shows what this picture shows: {spot}"},
        }
        res = the._ask(after, qs, cfg["model"], extra={spot: screen.jpeg(crop)})
        try:
            ctrl.look(-turn)
        finally:
            ctrl.release_all()
        if not res.get("ok"):
            return res
        same = jev.noul(res["answers"], "same")
        edges = jev.box(res["answers"], "spot")
        if same is None or same < 0.6 or not edges:
            return {"ok": False, "error": "Jev lost the spot after the turn: face something with clear detail and try a smaller turn_degrees."}
        moved = 0.5 - (edges["left"] + edges["right"]) / 2.0
        seen_deg = moved * policy.DEFAULTS["hfov"]
        if abs(seen_deg) < 1.0 or (seen_deg > 0) != (turn > 0):
            return {"ok": False, "error": "The view barely moved or moved the wrong way: check Fortnite has focus, then try again."}
        counts = round(cfg["counts"] * turn / seen_deg)
        counts = int(min(100000, max(50, counts)))
        _save_setting("look_counts", str(counts))
        the.manual("calibrate", after, {}, [{"look": turn}, {"look": -turn}], [], f"calibrate: turned {turn:g}°, saw {seen_deg:.1f}° -> {counts} counts per 90°", t0)
        return {"ok": True, "counts_per_90": counts, "was": int(cfg["counts"]), "asked_deg": turn,
                "seen_deg": round(seen_deg, 1), "confidence": edges.get("confidence"), "saved": True}

    # ---------------------------------------------------------------- runs

    def where(target_actor: str = "", target_xyz: Any = "") -> dict[str, Any]:
        pose = the.pose()
        if not pose:
            state = beacon_state()
            msg = ("The beacon printed, but not in the last few seconds: is the session still running?" if state["seen"]
                   else "No beacon position yet: add the Play Tester beacon device to the island.")
            return {"ok": False, "error": msg, "beacon": state, "how": BEACON_HOW}
        out: dict[str, Any] = {"ok": True, "pose": {k: round(float(pose[k]), 1) for k in ("x", "y", "z", "yaw", "pitch")},
                               "age_s": pose["age_s"]}
        place = the.target_point(target_actor, target_xyz)
        if not place.get("ok"):
            return {**out, "ok": False, "error": place.get("error")}
        if place.get("point"):
            rel = geo.relate(pose, place["point"])
            out.update({"target_point": place["point"], "rel": rel, "say": geo.describe(rel)})
        return out

    def add_beacon() -> dict[str, Any]:
        """Write the beacon's Verse file into the open project (Ducky's project writer, so Changes can undo it)."""
        try:
            from backend.uefn_plugins.host import get_contributions
            from frontend.ui_web.project_files import create_project_folder, create_project_verse_file
        except Exception:
            return {"ok": False, "error": "This UEFN Ducky can't write project files from a plugin: update the app."}
        row = next((r for r in get_contributions().get("verse_templates") or []
                    if r.get("plugin_id") == PLUGIN_ID and r.get("id") == "playtester_beacon"), None)
        content = ((row or {}).get("files") or [{}])[0].get("content") or (row or {}).get("content") or ""
        if not content:
            return {"ok": False, "error": "The beacon template is missing from the plugin: reinstall Play Tester."}
        folder = "Content/Verse/PlayTester"
        try:
            folder = create_project_folder("Content/Verse", "PlayTester")["path"]
        except ValueError as exc:
            if "Already exists" not in str(exc):
                return {"ok": False, "error": f"Could not make the PlayTester folder: {exc}"}
        try:
            made = create_project_verse_file(folder, "playtester_beacon_device.verse", content)
        except ValueError as exc:
            if "Already exists" in str(exc):
                return {"ok": True, "added": False, "path": f"{folder}/playtester_beacon_device.verse",
                        "message": "The beacon is already in the project."}
            return {"ok": False, "error": f"Could not write the beacon file: {exc}"}
        return {"ok": True, "added": True, "path": made["path"]}

    def beacon_tool(add: Any = False) -> dict[str, Any]:
        out: dict[str, Any] = {"ok": True, "beacon": beacon_state(), "template": "playtester_beacon",
                               "file": "Content/Verse/PlayTester/playtester_beacon_device.verse", "how": BEACON_HOW}
        if _bool(add):
            made = add_beacon()
            if not made.get("ok"):
                return {**out, "ok": False, "error": made["error"]}
            out["added"] = made
            out["next"] = ("Build Verse (workspace_compile_verse), push the changes, place playtester_beacon_device "
                           "in the level, then launch the session.")
        return out

    def run(goal: str = "", target: str = "", success: str = "", interact: Any = False, max_steps: Any = 0,
            max_seconds: Any = 0, dry_run: Any = False, wait_s: Any = 20, origin: str = "tool",
            target_actor: str = "", target_xyz: Any = "", success_print: str = "") -> dict[str, Any]:
        started = the.start(goal=goal, target=target, success=success, interact=_bool(interact),
                            max_steps=int(_num(max_steps)), max_seconds=_num(max_seconds), dry_run=_bool(dry_run),
                            origin=origin, target_actor=target_actor, target_xyz=target_xyz, success_print=success_print)
        if not started.get("ok"):
            return started
        wait = min(25.0, max(0.0, _num(wait_s, 20)))
        if not wait:
            return started
        out = the.wait(started["run_id"], wait)
        if out.get("ok") and not out.get("finished"):
            out["next"] = f"Still playing: playtester_wait('{started['run_id']}') for the result, or playtester_stop."
        if started.get("note"):
            out["note"] = started["note"]
        return out

    def get_run(run_id: str = "", from_step: Any = 0) -> dict[str, Any]:
        rid = runs.clean_id(run_id)
        if not rid:
            latest = store.latest()
            rid = str(latest.get("id")) if latest else ""
        doc = store.get(rid) if rid else None
        if not doc:
            return {"ok": False, "error": "No play test has run yet." if not rid else f"No run {rid}."}
        first = int(_num(from_step))
        steps = [s for s in doc.get("steps") or [] if int(s.get("n") or 0) > first]
        return {"ok": True, "run": {**{k: v for k, v in doc.items() if k != "steps"}, "steps": steps}}

    def list_runs(limit: Any = 10) -> dict[str, Any]:
        rows = store.summaries(int(min(50, max(1, _num(limit, 10)))))
        return {"ok": True, "runs": rows, "count": len(rows)}

    def frame_bytes(run_id: str = "", step: Any = 0) -> tuple[str, int, bytes | None]:
        rid = runs.clean_id(run_id)
        if not rid:
            latest = store.latest()
            rid = str(latest.get("id")) if latest else ""
        doc = store.get(rid) if rid else None
        if not doc:
            return rid, 0, None
        n = int(_num(step))
        if n <= 0:
            steps = doc.get("steps") or []
            n = int(steps[-1].get("n") or 0) if steps else 0
        if n <= 0:
            return rid, 0, None
        return rid, n, store.read_frame(rid, the.frame_slot(rid, n))

    def frame(run_id: str = "", step: Any = 0) -> dict[str, Any]:
        rid, n, data = frame_bytes(run_id, step)
        if data is None:
            return {"ok": False, "error": "No saved frame for that run and step."}
        path = store.frame_path(rid, the.frame_slot(rid, n))
        return {"ok": True, "run_id": rid, "step": n, "path": path,
                "files": [{"kind": "image", "path": path, "name": f"Play Tester {rid} step {n}"}]}

    # ---------------------------------------------------------------- MCP tools

    @api.tool(intent=INTENT, listener=False)
    def playtester_status() -> dict:
        """Check whether Play Tester can play test in game now: Fortnite client open and in play mode, which brain answers (TypeSafe key, Ducky AI or a gateway), model, running test."""
        return status()

    @api.tool(intent=INTENT, listener=False)
    def playtester_run(goal: str = "", target: str = "", success: str = "", interact: bool = False,
                       max_steps: int = 0, max_seconds: float = 0, dry_run: bool = False, wait_s: float = 20,
                       target_actor: str = "", target_xyz: str = "", success_print: str = "") -> dict:
        """Play test in game: Jev drives the Fortnite player toward a goal about once a second and returns what happened.

        goal: what to do ("walk to the red button and press it"). target: the thing to walk to
        ("the red button"), found with Jev's Box. target_actor: the level actor's label, or target_xyz
        "x,y,z" in centimetres: with the beacon device in the island the player is steered by world
        coordinates too, so targets behind the player, behind buildings or on another floor are found.
        success: a yes/no check Jev answers on each frame ("Is the door open?"); the run succeeds when it
        holds twice. success_print: text a Verse Print shows when the test passed ("coin collected",
        "[PTEVENT] door open"); the run passes the moment the island prints it, exact and instant.
        interact: hold E at the target. dry_run: decide and show moves without pressing
        anything. Waits up to wait_s (max 25) and returns the result or the run id to playtester_wait on.
        """
        return run(goal, target, success, interact, max_steps, max_seconds, dry_run, wait_s, "tool", target_actor, target_xyz,
                   success_print)

    @api.tool(intent=INTENT, listener=False)
    def playtester_map(refresh: bool = False) -> dict:
        """The island's layout for play tests: every device's label and position (x, y, z) read from UEFN, plus landmarks the beacon prints."""
        out = level_map(_bool(refresh))
        try:
            out["printed_landmarks"] = [{"name": k, **{c: round(v[c], 1) for c in ("x", "y", "z")}} for k, v in bcn.marks().items()]
        except Exception:
            out["printed_landmarks"] = []
        if out.get("actors") and len(out["actors"]) > 80:
            out["actors_shown"] = 80
            out["actors"] = out["actors"][:80]
        return out

    @api.tool(intent=INTENT, listener=False)
    def playtester_where(target_actor: str = "", target_xyz: str = "") -> dict:
        """Where the player is in the world (x, y, z, facing) from the beacon, and how far and which way a target is: behind, another floor."""
        return where(target_actor, target_xyz)

    @api.tool(intent=INTENT, listener=False)
    def playtester_beacon(add: bool = False) -> dict:
        """Check the Play Tester beacon device that reports the player's world position; add=true puts its Verse file in the project."""
        return beacon_tool(add)

    @api.tool(intent=INTENT, listener=False)
    def playtester_wait(run_id: str = "", timeout_s: float = 20) -> dict:
        """Wait (max 25 s) for a play test run to finish and return its result, steps and last frame."""
        return the.wait(run_id, timeout_s)

    @api.tool(intent=INTENT, listener=False)
    def playtester_stop(run_id: str = "") -> dict:
        """Stop the running play test (or a given run) after its current step; every key is released."""
        return the.stop(run_id)

    @api.tool(intent=INTENT, listener=False)
    def playtester_check(questions: dict | str = "") -> dict:
        """Look at the game once with Jev: answer your own yes/no, choice, score or box questions about the current Fortnite frame.

        questions: {id: {type: noul|choice|score|box, instructions, criteria?}}; call the frame `frame`,
        for example {"open": {"type": "noul", "instructions": "Is the door in `frame` open?"}}.
        """
        return check(questions)

    @api.tool(intent=INTENT, listener=False)
    def playtester_find(target: str = "") -> dict:
        """Find something on the Fortnite screen with Jev: is it visible, which side, and its box."""
        return find(target)

    @api.tool(intent=INTENT, listener=False)
    def playtester_press(keys: str = "", seconds: float = 0.5) -> dict:
        """Press and hold keys in the Fortnite client (W, A, S, D, SPACE, E, SHIFT, CTRL, digits) for 0.05-3 s; shown in the tab."""
        return press(keys, seconds)

    @api.tool(intent=INTENT, listener=False)
    def playtester_look(dx_degrees: float = 0, dy_degrees: float = 0) -> dict:
        """Turn the Fortnite camera with the mouse: dx_degrees right (negative = left), dy_degrees down (negative = up)."""
        return look(dx_degrees, dy_degrees)

    @api.tool(intent=INTENT, listener=False)
    def playtester_click(button: str = "", seconds: float = 0.05) -> dict:
        """Click or hold a mouse button in the Fortnite client: left fires or uses, right aims."""
        return click(button, seconds)

    @api.tool(intent=INTENT, listener=False)
    def playtester_calibrate(turn_degrees: float = 0) -> dict:
        """Measure the Fortnite mouse speed for Play Tester turns: turns turn_degrees (try 20), finds the same spot with Jev, saves it."""
        return calibrate(turn_degrees)

    @api.tool(intent=INTENT, listener=False)
    def playtester_lessons(target: str = "", limit: int = 10) -> dict:
        """What past play tests taught about a target: success rate, average steps, what blocked runs, where it was found, tips.

        Empty target: every target tested on this island with runs and wins. Read it before a
        playtester_run on a target that failed before; the next run of a target already starts
        from this memory (scan side, remembered position with the beacon).
        """
        return the.lessons(target, int(_num(limit, 10)))

    @api.tool(intent=INTENT, listener=False)
    def playtester_get_run(run_id: str = "", from_step: int = 0) -> dict:
        """Read a play test run step by step: Jev's answers, the keys and turns, the reason and timings (latest run when empty)."""
        return get_run(run_id, from_step)

    @api.tool(intent=INTENT, listener=False)
    def playtester_list_runs(limit: int = 10) -> dict:
        """List recent play test runs with their goal, result, steps and time."""
        return list_runs(limit)

    @api.tool(intent=INTENT, listener=False)
    def playtester_frame(run_id: str = "", step: int = 0) -> dict:
        """Get the saved Fortnite frame of a play test step as an image (last step of the latest run when empty)."""
        return frame(run_id, step)

    # ---------------------------------------------------------------- panel calls

    def rpc_frame(run_id: str = "", step: Any = 0, width: Any = 0) -> dict[str, Any]:
        rid, n, data = frame_bytes(run_id, step)
        if data is None:
            return {"ok": False, "error": "No saved frame."}
        return {"ok": True, "run_id": rid, "step": n, "src": screen.data_uri(data, int(_num(width)))}

    api.register_panel_rpc("status", lambda: status())
    api.register_panel_rpc("run", lambda goal="", target="", success="", interact=False, dry_run=False, max_steps=0,
                           target_actor="", target_xyz="", success_print="":
                           run(goal, target, success, interact, max_steps, 0, dry_run, 0, "tab", target_actor, target_xyz,
                               success_print))
    api.register_panel_rpc("stop", lambda run_id="": the.stop(run_id))
    api.register_panel_rpc("get_run", lambda run_id="", from_step=0: get_run(run_id, from_step))
    api.register_panel_rpc("list_runs", lambda limit=15: list_runs(limit))
    api.register_panel_rpc("frame", rpc_frame)
    api.register_panel_rpc("press", lambda keys="", seconds=0.3: press(keys, seconds))
    api.register_panel_rpc("look", lambda dx_degrees=0, dy_degrees=0: look(dx_degrees, dy_degrees))

    # ---------------------------------------------------------------- workflow nodes

    def _cfg(ctx: dict[str, Any]) -> dict[str, Any]:
        return ctx.get("config") if isinstance(ctx.get("config"), dict) else {}

    def _inputs(ctx: dict[str, Any]) -> dict[str, Any]:
        return ctx.get("inputs") if isinstance(ctx.get("inputs"), dict) else {}

    @api.register_pipeline_node("playtester.status")
    def node_status(ctx: dict) -> dict:
        out = status()
        return {**out, "ready": out["ready"], "next": out["next"]}

    @api.register_pipeline_node("playtester.check")
    def node_check(ctx: dict) -> dict:
        cfg, ins = _cfg(ctx), _inputs(ctx)
        out = check(ins.get("questions") or cfg.get("questions"))
        return {**out, "answers": out.get("answers") or {}}

    @api.register_pipeline_node("playtester.run")
    def node_run(ctx: dict) -> dict:
        cfg, ins = _cfg(ctx), _inputs(ctx)
        goal = str(ins.get("goal") or cfg.get("goal") or "")
        out = run(goal, str(cfg.get("target") or ""), str(cfg.get("success") or ""), cfg.get("interact"),
                  cfg.get("max_steps"), 0, cfg.get("dry_run"), 0, "workflow",
                  str(cfg.get("target_actor") or ""), str(cfg.get("target_xyz") or ""), str(cfg.get("success_print") or ""))
        if not out.get("ok"):
            return {**out, "status": "failed", "summary": out.get("error", "")}
        rid = out["run_id"]
        result = the.wait(rid, 25)
        deadline = time.time() + pilot.LIMIT_SECONDS + 30
        while result.get("ok") and not result.get("finished") and time.time() < deadline:
            result = the.wait(rid, 25)
        return {**result, "status": result.get("status"), "steps": result.get("n_steps"),
                "seconds": result.get("seconds"), "summary": result.get("reason") or result.get("last_why", ""),
                "frame": result.get("frame", "")}

    @api.register_pipeline_node("playtester.press")
    def node_press(ctx: dict) -> dict:
        cfg = _cfg(ctx)
        return press(cfg.get("keys") or "", _num(cfg.get("seconds"), 0.5))

    @api.register_pipeline_node("playtester.look")
    def node_look(ctx: dict) -> dict:
        cfg = _cfg(ctx)
        return look(_num(cfg.get("dx_degrees")), _num(cfg.get("dy_degrees")))

    @api.register_pipeline_node("playtester.stop")
    def node_stop(ctx: dict) -> dict:
        return the.stop(str(_cfg(ctx).get("run_id") or ""))

    @api.register_pipeline_node("playtester.map")
    def node_map(ctx: dict) -> dict:
        out = level_map(_bool(_cfg(ctx).get("refresh")))
        return {**out, "count": out.get("count", 0), "summary": f"{out.get('count', 0)} devices" if out.get("ok") else out.get("error", "")}

    @api.register_pipeline_node("playtester.lessons")
    def node_lessons(ctx: dict) -> dict:
        cfg = _cfg(ctx)
        out = the.lessons(str(cfg.get("target") or ""), int(_num(cfg.get("limit"), 10)))
        return {**out, "summary": "; ".join(out.get("tips") or []) or f"{out.get('runs', 0)} runs", "success_rate": out.get("success_rate")}

    @api.register_pipeline_node("playtester.where")
    def node_where(ctx: dict) -> dict:
        cfg = _cfg(ctx)
        out = where(str(cfg.get("target_actor") or ""), str(cfg.get("target_xyz") or ""))
        return {**out, "say": out.get("say") or out.get("error", ""), "distance_m": (out.get("rel") or {}).get("distance_m")}

    api.log("Play Tester tools, nodes and panel calls registered")


def _say(row: dict[str, Any]) -> str:
    if row.get("noul") is not None:
        return f"{row['noul']:.0%}"
    if row.get("choice"):
        p = (row.get("probabilities") or {}).get(row["choice"])
        return f"{row['choice']}" + (f" {p:.0%}" if p is not None else "")
    if row.get("score") is not None:
        return f"score {row['score']:.2f}"
    if row.get("box"):
        b = row["box"]
        return f"box x {b['left']:.2f}-{b['right']:.2f} y {b['top']:.2f}-{b['bottom']:.2f}"
    return "?"
