"""The Jev pilot: capture -> one Jev call -> plain-code decision -> input, about once a second.

A run plays on its own thread in whichever process started it (the app for the tab's
Start button, the agents' bridge for a chat's tool call). Everything another process
needs goes through the plugin's data: the run doc, the stop flag and the ``active``
lock, so the tab can follow and stop a run an agent started. Keys and buttons are
released on every way out.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from . import geo, jev, learning, policy, questions, runs, screen

LIMIT_STEPS = 120
LIMIT_SECONDS = 300.0
MANUAL = "manual"
MANUAL_KEEP = 40


class Game:
    """The real Fortnite client. Tests pass a fake with the same methods."""

    def status(self) -> dict[str, Any]:
        return screen.client()

    def ready(self, wait: bool = True) -> dict[str, Any]:
        return screen.ready(wait=wait)

    def focus(self, hwnd: int, allow_click: bool = True) -> dict[str, Any]:
        return screen.focus(hwnd, allow_click)

    def grab(self, hwnd: int, size: int) -> dict[str, Any]:
        return screen.grab(hwnd, size)

    def smaller(self, image: Any) -> bytes:
        return screen.smaller(image)

    def thumb(self, data: bytes) -> str:
        return screen.data_uri(data, 320)

    def draw_box(self, data: bytes, edges: dict[str, float] | None, label: str = "") -> bytes:
        return screen.draw_box(data, edges, label)

    def controller(self, hwnd: int, stop: threading.Event, counts: float) -> Any:
        from . import inputs

        return inputs.Controller(hwnd, counts_per_90=counts, stop=stop)


def _num(value: Any, default: float, low: float, high: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v <= 0:
        return default
    return min(high, max(low, v))


class Pilot:
    def __init__(self, store: runs.Runs, client: jev.JevClient, game: Any = None,
                 push: Callable[[dict[str, Any]], None] | None = None,
                 settings: Callable[[], dict[str, Any]] | None = None,
                 beacon: Any = None,
                 locate: Callable[[str], dict[str, Any]] | None = None,
                 save_setting: Callable[[str, Any], None] | None = None,
                 island: Callable[[], str] | None = None,
                 landmarks: Callable[[], list[dict[str, Any]]] | None = None) -> None:
        self.runs = store
        self.jev = client
        self.game = game if game is not None else Game()
        self._push = push
        self._settings = settings or (lambda: {})
        self.beacon = beacon
        self._locate = locate
        self._save_setting = save_setting or (lambda key, value: None)
        self._island = island or (lambda: "")
        self._landmarks = landmarks or (lambda: [])
        self._events: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._manual_lock = threading.Lock()

    # ------------------------------------------------------------------ helpers

    def push(self, event: dict[str, Any]) -> None:
        if self._push is None:
            return

        def send() -> None:
            try:
                self._push(event)
            except Exception:
                pass

        threading.Thread(target=send, name="playtester-push", daemon=True).start()

    def cfg(self) -> dict[str, Any]:
        s = self._settings() or {}
        return {
            "model": str(s.get("model") or jev.DEFAULT_MODEL).strip() or jev.DEFAULT_MODEL,
            "counts": _num(s.get("look_counts"), 2500.0, 50.0, 100000.0),
            "max_steps": int(_num(s.get("max_steps"), 60, 1, LIMIT_STEPS)),
            "confidence": _num(s.get("confidence"), 0.6, 0.3, 0.95),
            "size": int(_num(s.get("image_size"), 1024, screen.MIN_SIDE, screen.MAX_SIDE)),
            "click_focus": s.get("click_focus") not in (False, "false", "0", 0),
            "smooth": s.get("smooth_walk") not in (False, "false", "0", 0),
        }

    def _ready_client(self) -> dict[str, Any]:
        """The client in play mode and in front, or {ok: False, error}."""
        if not jev.api_key() and not getattr(self.jev, "_key", ""):
            return {"ok": False, "error": jev.NO_KEY, "needs_key": True}
        st = self.game.ready(True)
        if not st.get("ok"):
            return st
        hwnd = int(st["hwnd"])
        if not st.get("foreground"):
            res = self._focus(hwnd, self.cfg()["click_focus"])
            if not res.get("ok"):
                return {"ok": False, "error": res.get("error") or "Fortnite could not come to the front.", "focus": res}
        return {"ok": True, "hwnd": hwnd}

    def _focus(self, hwnd: int, allow_click: bool, run: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            res = self.game.focus(hwnd, allow_click) or {"ok": True, "method": "plain"}
        except Exception as exc:
            res = {"ok": False, "method": "error", "error": str(exc)[:200]}
        if run is not None:
            run.setdefault("events", []).append({"t": round(time.time() - float(run["started"]), 2), "kind": "focus",
                                                 "method": res.get("method"), "ok": bool(res.get("ok")), "ms": res.get("ms")})
        return res

    def island(self) -> str:
        try:
            return str(self._island() or "")
        except Exception:
            return ""

    def _ask(self, frame: dict[str, Any], qs: dict[str, Any], model: str, prev: bytes | None = None,
             extra: dict[str, bytes] | None = None, goal: str = "", facts: dict[str, Any] | None = None) -> dict[str, Any]:
        ref = jev.new_ref("frame")
        images = {ref: frame["jpeg"], **(extra or {})}
        state: dict[str, Any] = {"frame": ref}
        if goal:
            state["goal"] = goal
        if facts:
            state.update(facts)
        if prev:
            pref = jev.new_ref("previous")
            images[pref] = prev
            state["previous"] = pref
        return self.jev.decide(state, qs, images, model)

    def pose(self) -> dict[str, Any] | None:
        """The player's newest position and facing from the beacon, when it is printing."""
        if self.beacon is None:
            return None
        try:
            return self.beacon.fresh()
        except Exception:
            return None

    def target_point(self, target_actor: str = "", target_xyz: Any = "") -> dict[str, Any]:
        """{ok, point, source} for a level actor label or raw x,y,z; {ok: True, point: None} when neither is given."""
        if target_xyz not in ("", None):
            pt = geo.point(target_xyz)
            if not pt:
                return {"ok": False, "error": "target_xyz must be three numbers in centimetres: x,y,z."}
            return {"ok": True, "point": pt, "source": "xyz"}
        label = (target_actor or "").strip()
        if not label:
            return {"ok": True, "point": None}
        if self._locate is None:
            return {"ok": False, "error": "Can't look actors up here: give target_xyz instead."}
        found = self._locate(label)
        if not found.get("ok"):
            return found
        return {"ok": True, "point": found["point"], "source": "level", "label": found.get("label", label)}

    def _prints(self, since: float) -> list[dict[str, Any]]:
        reader = getattr(self.beacon, "prints_since", None)
        try:
            return list(reader(since)) if reader else []
        except Exception:
            return []

    def _beacon_marks(self) -> list[dict[str, Any]]:
        reader = getattr(self.beacon, "marks", None)
        try:
            return [{**v, "name": k, "source": "print"} for k, v in (reader() if reader else {}).items()]
        except Exception:
            return []

    def relation(self, pose: dict[str, Any] | None, point: dict[str, float] | None) -> dict[str, Any] | None:
        if not pose or not point:
            return None
        return geo.relate(pose, point)

    # ------------------------------------------------------------------ runs

    def start(self, *, goal: str = "", target: str = "", success: str = "", interact: bool = False,
              max_steps: int = 0, max_seconds: float = 0, dry_run: bool = False, origin: str = "tool",
              target_actor: str = "", target_xyz: Any = "", success_print: str = "") -> dict[str, Any]:
        goal, target, success = (goal or "").strip(), (target or "").strip(), (success or "").strip()
        success_print = (success_print or "").strip()
        if not goal and not target and not target_actor and target_xyz in ("", None):
            return {"ok": False, "error": "Give a goal, for example: walk to the red button and press it."}
        if not goal:
            goal = f"reach {target or target_actor or 'the target position'}"
        place = self.target_point(target_actor, target_xyz)
        if not place.get("ok"):
            return place
        if not jev.api_key() and not getattr(self.jev, "_key", ""):
            return {"ok": False, "error": jev.NO_KEY, "needs_key": True}
        st = self.game.status()
        if not st.get("ok"):
            return st
        if not st.get("gameplay_loaded"):
            return {"ok": False, "error": "Fortnite is still loading the island: wait until it is in play mode (playtester_status shows it)."}
        cfg = self.cfg()
        run_id = runs.new_id()
        held = self.runs.claim(run_id)
        if held:
            return {"ok": False, "error": f"Run {held.get('run_id')} is already playing: stop it first (playtester_stop).", "active": held.get("run_id")}
        now = time.time()
        island = self.island()
        beacon_on = self.pose() is not None
        mem = self.memory(island, target or target_actor) if (target or target_actor) else None
        seeded = learning.seed(mem, beacon_on=beacon_on, has_point=bool(place.get("point")))
        point = place.get("point") or seeded.get("point")
        run: dict[str, Any] = {
            "id": run_id, "goal": goal, "target": target, "success": success, "interact": bool(interact),
            "dry_run": bool(dry_run), "model": cfg["model"], "origin": origin,
            "max_steps": int(min(LIMIT_STEPS, max_steps or cfg["max_steps"])),
            "max_seconds": float(min(LIMIT_SECONDS, max_seconds or LIMIT_SECONDS)),
            "settings": {"confidence": cfg["confidence"], "counts": cfg["counts"], "size": cfg["size"]},
            "status": "running", "reason": "", "started": now, "ended": None, "n_steps": 0, "seconds": 0.0,
            "avg_ms": 0, "jev_ms": 0, "tokens": {"input": 0, "output": 0}, "last_frame": "", "steps": [],
            "target_actor": place.get("label") or (target_actor or "").strip(), "target_point": point,
            "beacon": beacon_on, "island": island, "click_focus": cfg["click_focus"],
            "memory_used": seeded or None, "events": [], "question_text": {},
            "success_print": success_print, "smooth": cfg["smooth"] and not dry_run,
        }
        self.runs.clear_stop(run_id)
        self.runs.save(run)
        stop = threading.Event()
        self._events[run_id] = stop
        thread = threading.Thread(target=self._loop, args=(run, stop, cfg), name=f"playtester-{run_id}", daemon=True)
        self._threads[run_id] = thread
        thread.start()
        self.push({"type": "playtester_run", "run": runs.summary(run)})
        out = {"ok": True, "run_id": run_id, "status": "running", "goal": goal, "dry_run": bool(dry_run),
               "max_steps": run["max_steps"], "model": cfg["model"], "coordinates": bool(run["target_point"] and run["beacon"]),
               "island": island}
        if seeded:
            out["memory_used"] = seeded
            if seeded.get("warn"):
                out["warn"] = seeded["warn"]
        if run["target_point"] and not run["beacon"]:
            out["note"] = ("The target has coordinates but no beacon is printing, so this run steers by the image only. "
                           "Add the Play Tester beacon device (playtester_beacon) to steer around buildings and between floors.")
        return out

    # ------------------------------------------------------------------ learning

    def memory(self, island: str, target: str) -> dict[str, Any] | None:
        doc = self.runs.data.get(learning.memory_key(island, target))
        return doc if isinstance(doc, dict) else None

    def learn(self, run: dict[str, Any]) -> dict[str, Any] | None:
        """Keep the run's lesson and fold it into the target's memory; never fails a run."""
        try:
            les = learning.lesson(run)
            self.runs.data.put(f"lesson.{run['id']}", les)
            name = run.get("target") or run.get("target_actor")
            if name:
                mk = learning.memory_key(run.get("island") or "", name)
                self.runs.data.put(mk, learning.fold(self.runs.data.get(mk), les))
            rows = sorted((self.runs.data.items("lesson.") or {}).values(), key=lambda r: float(r.get("ended") or 0), reverse=True)
            for old in rows[learning.KEEP_LESSONS:]:
                self.runs.data.delete(f"lesson.{old.get('id')}")
            return les
        except Exception:
            return None

    def lessons(self, target: str = "", limit: int = 10) -> dict[str, Any]:
        rows = sorted((self.runs.data.items("lesson.") or {}).values(), key=lambda r: float(r.get("ended") or 0), reverse=True)
        island = self.island()
        if target:
            tk = learning.key(target)
            mine = [r for r in rows if r.get("target_key") == tk or learning.key(r.get("target_actor")) == tk]
            here = [r for r in mine if (r.get("island") or "") == island] or mine
            mem = self.memory(island, target) or (self.memory(here[0].get("island") or "", target) if here else None)
            return {"ok": True, **learning.summary(mem, here[: max(1, int(limit))])}
        targets: dict[str, dict[str, Any]] = {}
        for r in rows:
            k = r.get("target_key") or "(no target)"
            t = targets.setdefault(k, {"target": r.get("target") or r.get("target_actor") or k, "runs": 0, "wins": 0, "last": r.get("outcome")})
            t["runs"] += 1
            t["wins"] += 1 if r.get("outcome") == "success" and not r.get("dry_run") else 0
        return {"ok": True, "island": island, "runs": len(rows), "targets": list(targets.values())[:50],
                "recent": [{k: r.get(k) for k in ("id", "goal", "outcome", "reason", "steps", "seconds")} for r in rows[: max(1, int(limit))]]}

    def stop(self, run_id: str = "") -> dict[str, Any]:
        rid = runs.clean_id(run_id)
        if not rid:
            held = self.runs.active()
            rid = str(held.get("run_id")) if held else ""
        if not rid:
            return {"ok": True, "stopped": False, "message": "Nothing is playing."}
        doc = self.runs.get(rid)
        if not doc:
            return {"ok": False, "error": f"No run {rid}."}
        if doc.get("status") in runs.DONE:
            return {"ok": True, "stopped": False, "run_id": rid, "status": doc.get("status"), "message": "That run already finished."}
        self.runs.request_stop(rid)
        evt = self._events.get(rid)
        if evt:
            evt.set()
        return {"ok": True, "stopped": True, "run_id": rid, "message": "Stopping after the current step; keys are released."}

    def wait(self, run_id: str = "", timeout_s: float = 20.0) -> dict[str, Any]:
        rid = runs.clean_id(run_id)
        if not rid:
            latest = self.runs.latest()
            rid = str(latest.get("id")) if latest else ""
        if not rid:
            return {"ok": False, "error": "No play test has run yet: start one with playtester_run."}
        deadline = time.time() + min(25.0, max(0.0, float(timeout_s or 0)))
        while True:
            row = self.runs.data.get(f"sum.{rid}")
            if not isinstance(row, dict):
                return {"ok": False, "error": f"No run {rid}."}
            if row.get("status") in runs.DONE or time.time() >= deadline:
                break
            time.sleep(0.25)
        return self.result(rid)

    def result(self, run_id: str) -> dict[str, Any]:
        run = self.runs.get(run_id)
        if not run:
            return {"ok": False, "error": f"No run {runs.clean_id(run_id)}."}
        steps = run.get("steps") or []
        last = steps[-1] if steps else {}
        out = {"ok": True, **runs.summary(run), "finished": run.get("status") in runs.DONE,
               "last_why": last.get("why", ""), "last_view": last.get("view", {}),
               "moves": [_short(s) for s in steps[-12:]]}
        if last.get("where"):
            out["last_where"] = last["where"]
        if run.get("totals"):
            out["totals"] = learning.lesson(run)["totals"]
            out["found"] = run.get("found")
        if run.get("memory_used"):
            out["memory_used"] = run["memory_used"]
        if run.get("events"):
            out["focus_events"] = [e for e in run["events"] if e.get("kind") == "focus"][-5:]
        if run.get("last_frame"):
            out["frame"] = run["last_frame"]
        return out

    def _finish(self, run: dict[str, Any], status: str, reason: str) -> None:
        run["status"] = status
        run["reason"] = reason
        run["ended"] = time.time()
        run["seconds"] = round(run["ended"] - float(run["started"]), 1)
        self.runs.save(run)
        self.learn(run)
        self.push({"type": "playtester_run", "run": runs.summary(run)})

    def _loop(self, run: dict[str, Any], stop: threading.Event, cfg: dict[str, Any]) -> None:
        rid = run["id"]
        goal = {"target": run["target"], "success": run["success"] or run.get("success_print") or "",
                "interact": run["interact"], "settings": {"confidence": cfg["confidence"]}}
        try:
            level_marks = list(self._landmarks() or [])
        except Exception:
            level_marks = []
        seen_prints = 0
        memory = policy.new_memory()
        seeded = run.get("memory_used") or {}
        if seeded.get("scan_dir"):
            memory["scan_dir"] = int(seeded["scan_dir"])
        prev: bytes | None = None
        errors = 0
        ctrl = None
        n = 0
        point = run.get("target_point")
        last_pose: dict[str, Any] | None = None
        last_turn = 0.0      # degrees the previous step turned the camera
        calibrated = 0
        try:
            st = self.game.ready(True)
            if not st.get("ok"):
                return self._finish(run, "failed", str(st.get("error") or "Fortnite is not ready"))
            hwnd = int(st["hwnd"])
            ctrl = self.game.controller(hwnd, stop, cfg["counts"])
            while True:
                if stop.is_set() or self.runs.stop_requested(rid):
                    return self._finish(run, "stopped", "Stopped from Play Tester.")
                elapsed = time.time() - float(run["started"])
                if n >= run["max_steps"] or elapsed >= run["max_seconds"]:
                    limit = f"{run['max_steps']} steps" if n >= run["max_steps"] else f"{run['max_seconds']:g} s"
                    if not run["target"] and not run["success"] and not point:
                        return self._finish(run, "done", f"Played the goal for {limit}.")
                    return self._finish(run, "timeout", f"Hit the {limit} limit before the goal was met.")
                t0 = time.perf_counter()
                status = self.game.status()
                if not status.get("ok"):
                    return self._finish(run, "failed", str(status.get("error") or "The Fortnite client closed."))
                if not status.get("foreground"):
                    res = self._focus(hwnd, cfg["click_focus"], run)
                    if not res.get("ok"):
                        return self._finish(run, "failed", str(res.get("error") or "Fortnite could not come to the front."))
                frame = self.game.grab(hwnd, cfg["size"])

                # What the island printed since the last step: a success print ends the run.
                prints = self._prints(run["started"])
                for pr in prints[seen_prints:][-20:]:
                    run["events"].append({"t": round(pr["t"] - float(run["started"]), 2), "kind": "print", "text": pr["text"][:200]})
                seen_prints = len(prints)
                if run.get("success_print"):
                    hit = next((pr for pr in prints if run["success_print"].lower() in pr["text"].lower()), None)
                    if hit:
                        return self._finish(run, "success", f"Verse printed: {hit['text'][:160]}")

                # Where the player is (beacon) and where the target is (level), when both are known.
                pose = self.pose()
                new_pose = bool(pose and (not last_pose or pose.get("tick") != last_pose.get("tick")))
                rel = self.relation(pose, point)
                where: dict[str, Any] = {}
                if pose:
                    where["pose"] = {k: round(float(pose[k]), 1) for k in ("x", "y", "z", "yaw")}
                    where["pose"]["age_s"] = pose.get("age_s")
                if rel:
                    where["rel"] = rel
                if pose and pose.get("state"):
                    where["pose"]["state"] = pose["state"]
                marks = level_marks + self._beacon_marks()
                near = geo.nearby(pose, marks, 3, skip=run.get("target_actor") or "") if pose else []
                if near:
                    where["nearby"] = [{k: r[k] for k in ("name", "x", "y", "z", "distance_m", "side", "height_m")} for r in near]
                if new_pose and last_pose:
                    where["moved_m"] = geo.moved_m(last_pose, pose)
                    where["climb_m"] = round((float(pose["z"]) - float(last_pose["z"])) / 100.0, 2)
                    calib = self._calibrate(ctrl, last_turn, last_pose, pose)
                    if calib:
                        where["calib"] = calib
                        calibrated += 1
                        run["settings"]["counts"] = calib["counts"]
                facts: dict[str, Any] = {}
                if rel:
                    facts["target_place"] = geo.describe(rel)
                if pose:
                    climb = where.get("climb_m")
                    facts["player"] = (f"{pose.get('state') or 'moving'}, height {float(pose['z']) / 100:.1f} m"
                                       + (f", {'climbing' if climb > 0 else 'dropping'} {abs(climb):.1f} m since the last step" if climb and abs(climb) >= 0.2 else ""))
                if near:
                    facts["nearby"] = geo.nearby_text(near)
                facts = facts or None

                box_ok = self.jev.box_ok(run["model"])
                floor = rel["floor"] if rel else ""
                qs = questions.step_questions(goal=run["goal"], target=run["target"], success=run["success"],
                                              interact=run["interact"], has_previous=prev is not None, box_ok=box_ok, floor=floor)
                res = self._ask(frame, qs, run["model"], prev, goal=run["goal"], facts=facts)
                if not res.get("ok") and box_ok and int(res.get("status") or 0) == 400 and "box" in str(res.get("error")).lower():
                    self.jev.no_box.add(run["model"])  # this model has no Box: fall back to left/center/right
                    qs = questions.step_questions(goal=run["goal"], target=run["target"], success=run["success"],
                                                  interact=run["interact"], has_previous=prev is not None, box_ok=False, floor=floor)
                    res = self._ask(frame, qs, run["model"], prev, goal=run["goal"], facts=facts)
                n += 1
                run["question_text"].update({k: str(v.get("instructions") or "") for k, v in qs.items()})
                if not res.get("ok"):
                    errors += 1
                    run["events"].append({"t": round(time.time() - float(run["started"]), 2), "kind": "jev_error",
                                          "status": res.get("status"), "error": str(res.get("error") or "")[:200]})
                    self._record(run, n, frame, {}, {"actions": [], "why": f"Jev: {res.get('error')}", "aim": None}, [], t0, res,
                                 error=True, where=where, qs=qs)
                    if res.get("needs_key") or int(res.get("status") or 0) in (401, 403) or errors >= 3:
                        return self._finish(run, "failed", str(res.get("error")))
                    time.sleep(0.5)
                    continue
                errors = 0
                view = questions.read_view(res.get("answers") or {})
                if rel:
                    view["geo"] = rel
                if where.get("moved_m") is not None:
                    view["moved_m"] = where["moved_m"]
                if where.get("climb_m") is not None:
                    view["climb_m"] = where["climb_m"]
                dec = policy.decide(view, memory, goal)
                memory = dec["memory"]
                done: list[dict[str, Any]] = []
                if not run["dry_run"] and dec["actions"]:
                    done = ctrl.run(dec["actions"], smooth=bool(run.get("smooth"))) if run.get("smooth") else ctrl.run(dec["actions"])
                for ev in (ctrl.drain_events() if hasattr(ctrl, "drain_events") else []):
                    run["events"].append({"t": round(time.time() - float(run["started"]), 2), **ev})
                last_turn = sum(float((d.get("look") or {}).get("dx_deg") or 0) for d in done)
                if new_pose:
                    last_pose = pose
                self._record(run, n, frame, view, dec, done, t0, res, where=where, qs=qs)
                prev = self.game.smaller(frame["image"])
                if dec.get("finish"):
                    return self._finish(run, dec["finish"]["status"], dec["finish"]["reason"])
                if run["dry_run"]:
                    time.sleep(0.4)
        except Exception as exc:
            return self._finish(run, "failed", f"Play Tester stopped on an error: {type(exc).__name__}: {str(exc)[:200]}")
        finally:
            if ctrl is not None:
                try:
                    ctrl.release_all()
                except Exception:
                    pass
            if calibrated >= 2:
                try:  # the mouse speed the beacon measured is better than the old setting
                    self._save_setting("look_counts", str(int(run["settings"]["counts"])))
                except Exception:
                    pass
            self.runs.release(rid)
            self._events.pop(rid, None)
            self._threads.pop(rid, None)
            try:
                self.runs.prune()
            except Exception:
                pass

    @staticmethod
    def _calibrate(ctrl: Any, turned: float, before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any] | None:
        """Compare the turn we asked for with the yaw change the beacon saw; nudge the mouse speed."""
        if ctrl is None or abs(turned) < 15.0:
            return None
        seen = geo.wrap(float(after["yaw"]) - float(before["yaw"]))
        if abs(seen) < 3.0 or (seen > 0) != (turned > 0):
            return None
        ratio = turned / seen
        if not 0.2 <= ratio <= 5.0:
            return None
        old = float(getattr(ctrl, "counts_per_90", 0) or 0)
        if old <= 0:
            return None
        new = min(100000.0, max(50.0, 0.5 * old + 0.5 * old * ratio))
        ctrl.counts_per_90 = new
        return {"asked_deg": round(turned, 1), "seen_deg": round(seen, 1), "counts": int(round(new)), "was": int(round(old))}

    def _record(self, run: dict[str, Any], n: int, frame: dict[str, Any], view: dict[str, Any], dec: dict[str, Any],
                done: list[dict[str, Any]], t0: float, res: dict[str, Any], *, error: bool = False,
                where: dict[str, Any] | None = None, qs: dict[str, Any] | None = None) -> None:
        path = self.runs.save_frame(run["id"], n, frame["jpeg"])
        ms = int((time.perf_counter() - t0) * 1000)
        step = {
            "n": n, "t": round(time.time() - float(run["started"]), 2), "ms": ms,
            "capture_ms": int(frame.get("ms") or 0), "jev_ms": int(res.get("ms") or 0),
            "view": view, "why": dec.get("why", ""), "actions": dec.get("actions") or [], "done": done,
            "aim": dec.get("aim"), "dry_run": bool(run["dry_run"]), "size": [frame.get("width"), frame.get("height")],
        }
        step["rule"] = dec.get("rule") or ""
        step["model"] = res.get("model") or run.get("model")
        step["q"] = sorted((qs or {}).keys())
        step["raw"] = res.get("answers") or {}
        cut = [v.get("cut_short") for d in done if isinstance(d, dict) for v in d.values() if isinstance(v, dict) and v.get("cut_short")]
        if cut:
            step["cut"] = cut[0]
            run.setdefault("events", []).append({"t": step["t"], "kind": "cut_short", "why": cut[0], "step": n})
        if where:
            step["where"] = where
        if error:
            step["error"] = True
        usage = res.get("usage") or {}
        run["tokens"]["input"] += int(usage.get("input_tokens") or 0)
        run["tokens"]["output"] += int(usage.get("output_tokens") or 0)
        run["steps"].append(step)
        learning.tally(run, step, float((run.get("settings") or {}).get("confidence") or 0.6))
        k = len(run["steps"])
        run["n_steps"] = k
        run["avg_ms"] = int(sum(s["ms"] for s in run["steps"]) / k)
        run["jev_ms"] = int(sum(s["jev_ms"] for s in run["steps"]) / k)
        run["seconds"] = round(time.time() - float(run["started"]), 1)
        run["last_frame"] = path
        self.runs.save(run)
        self.runs.beat(run["id"])
        self.push({"type": "playtester_step", "run_id": run["id"], "status": run["status"], "n_steps": k,
                   "step": step, "thumb": self.game.thumb(frame["jpeg"])})

    # ------------------------------------------------------------------ one-shot actions

    def manual(self, kind: str, frame: dict[str, Any] | None, view: dict[str, Any], actions: list[dict[str, Any]],
               done: list[dict[str, Any]], why: str, t0: float, jev_ms: int = 0, jpeg: bytes | None = None) -> dict[str, Any]:
        """Log a single tool action as a step of the 'manual' run, so the tab shows it too."""
        with self._manual_lock:
            doc = self.runs.get(MANUAL) or {
                "id": MANUAL, "goal": "Single actions from tools and the tab", "target": "", "success": "",
                "interact": False, "dry_run": False, "model": self.cfg()["model"], "origin": "manual",
                "status": "manual", "reason": "", "started": time.time(), "ended": None, "n_steps": 0,
                "seconds": 0.0, "avg_ms": 0, "jev_ms": 0, "last_frame": "", "steps": [], "count": 0,
            }
            doc["count"] = int(doc.get("count") or 0) + 1
            n = doc["count"]
            path = ""
            if frame is not None:
                path = self.runs.save_frame(MANUAL, n % MANUAL_KEEP, jpeg or frame["jpeg"])
                doc["last_frame"] = path
            step = {"n": n, "slot": n % MANUAL_KEEP, "t": round(time.time() - float(doc["started"]), 2),
                    "ms": int((time.perf_counter() - t0) * 1000), "jev_ms": int(jev_ms),
                    "capture_ms": int((frame or {}).get("ms") or 0), "kind": kind, "view": view, "why": why,
                    "actions": actions, "done": done, "aim": None, "dry_run": False,
                    "size": [(frame or {}).get("width"), (frame or {}).get("height")]}
            doc["steps"] = (doc.get("steps") or [])[-(MANUAL_KEEP - 1):] + [step]
            doc["n_steps"] = n
            self.runs.save(doc)
        thumb = self.game.thumb(jpeg or frame["jpeg"]) if frame is not None else ""
        self.push({"type": "playtester_step", "run_id": MANUAL, "status": "manual", "n_steps": n, "step": step, "thumb": thumb})
        return {"path": path, "n": n}

    def frame_slot(self, run_id: str, n: int) -> int:
        return int(n) % MANUAL_KEEP if runs.clean_id(run_id) == MANUAL else int(n)


def _short(step: dict[str, Any]) -> str:
    """One line per step for agents: '#7 turn right 17°, W 0.9 s (840 ms)'."""
    parts: list[str] = []
    for act in step.get("actions") or []:
        if act.get("look"):
            parts.append(f"turn {'right' if act['look'] > 0 else 'left'} {abs(act['look']):g}°")
        elif act.get("keys"):
            parts.append(f"{'+'.join(act['keys'])} {act.get('seconds', 0):g} s")
        elif act.get("click"):
            parts.append(f"{act['click']} click")
        elif act.get("wait"):
            parts.append(f"wait {act['wait']:g} s")
    moves = ", ".join(parts) or "no move"
    flag = " (dry run)" if step.get("dry_run") else ""
    return f"#{step.get('n')} {moves}{flag} ({step.get('ms')} ms): {step.get('why', '')}"
