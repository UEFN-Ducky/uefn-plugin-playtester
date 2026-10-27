"""One tester: a target (the game it plays), its runs, its pilot, its own Who answers and settings.

The tools, panel calls and workflow nodes in ``__init__`` pick a tester (testers.TesterManager) and
call these methods. The default tester is the Fortnite client and keeps the plugin's original data
keys, so runs, lessons and maps from before testers existed are still there.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable

from . import brains, geo, jev, pilot, policy, questions, runs, screen
from .lease import NoLease

DEFAULT = "default"
BEACON_HOW = (
    "1) playtester_beacon(add=true) adds Content/Verse/PlayTester/playtester_beacon_device.verse to the project. "
    "2) Build Verse and push the changes. 3) Place playtester_beacon_device anywhere in the level. "
    "4) Launch Session: its [PTPOS] lines reach the UEFN Output Log and playtester_status shows the beacon on."
)
# Settings a tester keeps for itself (another game, another mouse speed); the rest are the plugin's.
OWN_SETTINGS = ("look_counts",)


def to_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def to_num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def questions_arg(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return None
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    return raw if isinstance(raw, dict) and raw else None


def compact(answers: dict[str, Any]) -> dict[str, Any]:
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


def say(row: dict[str, Any]) -> str:
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


class Tester:
    def __init__(self, tid: str, doc: dict[str, Any], *, store: runs.Runs, target: Any, settings: Callable[[], dict[str, Any]],
                 save_setting: Callable[[str, Any], None], push: Callable[[dict[str, Any]], None] | None = None,
                 lease: Any = None, recorder: Any = None, client: Any = None,
                 save_doc: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.id = tid
        self.doc = dict(doc or {})
        self.store = store
        self.target = target
        self.lease = lease if lease is not None else NoLease()
        self._plugin_settings = settings
        self._plugin_save = save_setting
        self._save_doc = save_doc or (lambda d: None)
        self.brain = client if client is not None else brains.Brain(jev.JevClient(), self.settings)
        self.pilot = pilot.Pilot(store, self.brain, game=target,
                                 push=(lambda e: push({**e, "tester": tid})) if push else None,
                                 settings=self.settings, save_setting=self.save_setting,
                                 recorder=recorder if getattr(target, "recorder_target", "") else None,
                                 lease=self.lease, who=tid)

    # ------------------------------------------------------------------ identity and settings

    @property
    def label(self) -> str:
        return str(self.doc.get("label") or getattr(self.target, "label", "") or self.id)

    @property
    def default(self) -> bool:
        return self.id == DEFAULT

    def settings(self) -> dict[str, Any]:
        """The plugin's settings, this tester's own ones over them, and its Who answers pick."""
        own = self.doc.get("settings") if isinstance(self.doc.get("settings"), dict) else {}
        pick = self.doc.get("brain") if isinstance(self.doc.get("brain"), dict) else {}
        return {**(self._plugin_settings() or {}), **own, **pick}

    def save_setting(self, key: str, value: Any) -> None:
        if self.default or key not in OWN_SETTINGS:
            self._plugin_save(key, value)
            return
        self.doc.setdefault("settings", {})[key] = value
        self._save_doc(self.doc)

    def set_brain(self, pick: str = "", brain: str = "") -> dict[str, Any]:
        """Who answers this tester: a model-picker pick or a brain name. The default tester keeps it in Settings."""
        values = brains.from_pick(pick) if pick else {"brain": str(brain or "").strip().lower()}
        if not values or values["brain"] not in brains.BRAINS:
            return {"ok": False, "error": f"Pick a model, or a brain: {', '.join(brains.BRAINS)}"}
        if self.default:
            for key, value in values.items():
                self._plugin_save(key, value)
        else:
            self.doc["brain"] = values
            self._save_doc(self.doc)
        return {"ok": True, "pick": brains.to_pick(self.settings())}

    def summary(self) -> dict[str, Any]:
        active = self.store.active()
        latest = self.store.latest()
        return {"id": self.id, "label": self.label, "default": self.default, "target": self.target.info(),
                "running": active.get("run_id") if active else None,
                "waiting": bool(self.lease.waiting(self.id)),
                "last": {k: latest.get(k) for k in ("id", "status", "goal")} if latest else None}

    # ------------------------------------------------------------------ status

    def beacon_state(self) -> dict[str, Any] | None:
        bcn = getattr(self.target, "beacon", None)
        if bcn is None:
            return None
        raw = bcn.pose()
        from . import beacon as beacon_mod

        fresh = raw is not None and raw["age_s"] <= beacon_mod.FRESH_S
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

    def status(self) -> dict[str, Any]:
        t = self.target
        st = t.status()
        cfg = self.pilot.cfg()
        brain = self.brain.ready()
        key = bool(brain.get("ok"))
        age = st.get("loaded_for_s")
        settle = screen.SETTLE_S if t.kind == "fortnite" else float(getattr(t, "settle_s", 0.0))
        playing = bool(st.get("ok") and st.get("gameplay_loaded") and (age or 0) >= settle)
        loading = bool(st.get("ok") and not playing)
        active = self.store.active()
        bcn = self.beacon_state()
        # The tab's two short lines when the game isn't open: what is wrong, what to do.
        said = str(st.get("error") or "")
        title = str(st.get("title") or (said.split(":")[0] if 0 < said.find(":") < 60 else f"{self.label} isn't open"))
        fix = str(st.get("fix") or (said.split(":", 1)[1].strip() if 0 < said.find(":") < 60 else said))
        # Who answers frames now (Auto worked out), named and drawn like the chat's model button.
        answering = str((active and getattr(self.brain, "last_brain", "")) or brain.get("brain") or "")
        gw = brain.get("gateway")
        gateway = tuple(gw) if isinstance(gw, (list, tuple)) and len(gw) == 2 else ("", "")
        now = brains.answering_pick(answering, cfg["model"], gateway) if key else ""
        via, logo = brains.gateway_face(now.partition(":")[0]) if now else ("", "")
        if not key:
            nxt = str(brain.get("error") or jev.NO_KEY)
        elif not st.get("ok"):
            nxt = str(st.get("error") or f"{self.label} isn't open")
        elif loading:
            nxt = f"{self.label} is loading: wait, then call playtester_status again."
        elif active:
            nxt = f"Run {active.get('run_id')} is playing: playtester_wait or playtester_stop."
        elif bcn is not None and not bcn["on"]:
            nxt = ("Ready, but the beacon is off, so runs see only the image: set it up first "
                   "(playtester_beacon(add=true), build Verse, push, place playtester_beacon_device, launch again), "
                   "then call playtester_run(goal, target, success).")
        else:
            nxt = "Ready: call playtester_run(goal, target, success) to play test."
        if not self.default and "tester=" not in nxt and nxt.startswith(("Ready", "Run ")):
            nxt += f" Pass tester={self.id!r}."
        out = {
            "ok": True,
            "tester": {"id": self.id, "label": self.label, "default": self.default},
            "target": t.info(),
            "ready": bool(key and playing and not active),
            "client": {"open": bool(st.get("ok")), "playing": playing, "loading": loading,
                       "foreground": bool(st.get("foreground")), "loaded_for_s": age,
                       "error": None if st.get("ok") else st.get("error"),
                       "title": None if st.get("ok") else title, "fix": None if st.get("ok") else fix},
            "jev": {"key": key, "own_key": bool(jev.api_key()),
                    "brain": answering or None,
                    "choice": brain.get("choice"), "pick": brains.to_pick(self.settings()), "model": cfg["model"],
                    "box": self.brain.box_ok(cfg["model"]),
                    "gateway": ":".join(brain["gateway"]) if brain.get("brain") == "gateway" else None,
                    "now": now, "name": brains.model_name(now), "via": via, "logo": logo,
                    "plan": bool(brain.get("plan")), "error": None if key else brain.get("error")},
            "beacon": bcn or {"seen": False, "on": False, "none": True},
            "waiting": bool(self.lease.waiting(self.id)),
            "settings": {k: cfg[k] for k in ("counts", "max_steps", "confidence", "size", "click_focus", "smooth")},
            "map": {"landmarks": len(t.landmarks())},
            "island": self.pilot.island(),
            "active_run": active.get("run_id") if active else None,
            "latest_run": self.store.latest(),
            "next": nxt,
        }
        return out

    # ------------------------------------------------------------------ looking

    def _with_input(self, work: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        """One-off work that needs the game in front: take the input lease, then give it back."""
        needs = bool(getattr(self.target, "needs_focus", True))
        if not needs:
            return work()
        try:
            with self.lease.turn(self.id, needed=True):
                return work()
        finally:
            self.lease.release(self.id)

    def check(self, qs: Any = None, label: str = "check") -> dict[str, Any]:
        parsed = questions_arg(qs)
        if parsed is None:
            return {"ok": False, "error": "Give questions: {id: {type: noul|choice|score|box, instructions, criteria?}} and call the frame `frame`."}
        problem = jev.check_questions(parsed)
        if problem:
            return {"ok": False, "error": problem}
        the = self.pilot
        t0 = time.perf_counter()

        def look() -> dict[str, Any]:
            ready = the._ready_client()
            if not ready.get("ok"):
                return ready
            return {"ok": True, "frame": the.game.grab(ready["hwnd"], the.cfg()["size"])}

        got = self._with_input(look)
        if not got.get("ok"):
            return got
        frame = got["frame"]
        res = the._ask(frame, parsed, the.cfg()["model"])
        if not res.get("ok"):
            return res
        answers = compact(res.get("answers") or {})
        boxes = {k: v["box"] for k, v in answers.items() if v.get("box")}
        first = next(iter(boxes.items()), None)
        shown = the.game.draw_box(frame["jpeg"], first[1] if first else None, first[0] if first else "")
        view = questions.read_view(res.get("answers") or {})
        why = f"{label}: " + ", ".join(f"{k} {say(v)}" for k, v in answers.items())
        logged = the.manual(label, frame, view, [], [], why, t0, int(res.get("ms") or 0), jpeg=shown)
        return {"ok": True, "answers": answers, "boxes": boxes, "jev_ms": res.get("ms"),
                "ms": int((time.perf_counter() - t0) * 1000), "frame": logged["path"],
                "size": [frame["width"], frame["height"]]}

    def find(self, target: str = "") -> dict[str, Any]:
        t = (target or "").strip()
        if not t:
            return {"ok": False, "error": "Give a target to find, for example: the red button."}
        box_ok = self.brain.box_ok(self.pilot.cfg()["model"])
        qs: dict[str, Any] = {
            "seen": {"type": "noul", "instructions": f"Is {t} visible in `frame`?"},
            "where": {"type": "choice", "instructions": f"Where is {t} in `frame`?", "criteria": questions.WHERE},
        }
        if box_ok:
            qs["target"] = {"type": "box", "instructions": f"{t} in `frame`"}
        out = self.check(qs, label=f"find {t}")
        if not out.get("ok"):
            return out
        ans = out["answers"]
        seen = (ans.get("seen") or {}).get("noul")
        box = (ans.get("target") or {}).get("box")
        found = seen is not None and seen >= self.pilot.cfg()["confidence"]
        result = {"ok": True, "target": t, "found": found, "seen": seen,
                  "where": (ans.get("where") or {}).get("choice"),
                  "box": box if found else None, "frame": out["frame"], "jev_ms": out["jev_ms"], "ms": out["ms"]}
        if box and not found:
            result["note"] = "Jev always returns a box; it is ignored because the target is not visible."
        return result

    # ------------------------------------------------------------------ pressing

    def act(self, kind: str, actions: list[dict[str, Any]], why: str) -> dict[str, Any]:
        active = self.store.active()
        if active:
            return {"ok": False, "error": f"Run {active.get('run_id')} is playing: stop it before pressing keys by hand."}
        the = self.pilot
        t0 = time.perf_counter()

        def press() -> dict[str, Any]:
            ready = the._ready_client()
            if not ready.get("ok"):
                return ready
            ctrl = the.game.controller(ready["hwnd"], threading.Event(), the.cfg()["counts"])
            try:
                done = ctrl.run(actions)
            except (ValueError, RuntimeError) as exc:
                return {"ok": False, "error": str(exc)}
            finally:
                ctrl.release_all()
            time.sleep(0.15)
            return {"ok": True, "done": done, "frame": the.game.grab(ready["hwnd"], the.cfg()["size"])}

        got = self._with_input(press)
        if not got.get("ok"):
            return got
        logged = the.manual(kind, got["frame"], {}, actions, got["done"], why, t0)
        return {"ok": True, "done": got["done"], "frame": logged["path"], "ms": int((time.perf_counter() - t0) * 1000)}

    def press(self, keys: Any = "", seconds: float = 0.5) -> dict[str, Any]:
        from . import inputs

        try:
            names = inputs.key_names(keys)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if not names:
            return {"ok": False, "error": "Give keys to press, for example W, W+A, SPACE or E."}
        secs = min(inputs.MAX_HOLD_S, max(inputs.MIN_HOLD_S, to_num(seconds, 0.5)))
        return self.act("press", [{"keys": names, "seconds": secs}], f"press {'+'.join(names)} {secs:g} s")

    def look(self, dx_degrees: float = 0.0, dy_degrees: float = 0.0) -> dict[str, Any]:
        dx, dy = to_num(dx_degrees), to_num(dy_degrees)
        if not dx and not dy:
            return {"ok": False, "error": "Give dx_degrees (right is positive) or dy_degrees (down is positive)."}
        dx, dy = max(-360.0, min(360.0, dx)), max(-90.0, min(90.0, dy))
        return self.act("look", [{"look": dx, "look_y": dy}],
                        f"turn {'right' if dx >= 0 else 'left'} {abs(dx):g}°" + (f", {'down' if dy > 0 else 'up'} {abs(dy):g}°" if dy else ""))

    def click(self, button: str = "", seconds: float = 0.05, x: Any = None, y: Any = None) -> dict[str, Any]:
        name = (button or "").strip().lower() or "left"
        if name not in ("left", "right"):
            return {"ok": False, "error": "Give button: left (fire / use) or right (aim)."}
        if x not in (None, "") and y not in (None, ""):
            px, py = min(1.0, max(0.0, to_num(x))), min(1.0, max(0.0, to_num(y)))
            return self.act("click", [{"click_at": [px, py], "click": name}], f"{name} click at {px:.0%}, {py:.0%}")
        secs = min(3.0, max(0.02, to_num(seconds, 0.05)))
        return self.act("click", [{"click": name, "seconds": secs}], f"{name} click {secs:g} s")

    def calibrate(self, turn_degrees: float = 0.0) -> dict[str, Any]:
        """Turn a known amount and find the same spot again with Jev, to learn the mouse speed."""
        turn = to_num(turn_degrees)
        if not 5 <= abs(turn) <= 40:
            return {"ok": False, "error": "Give turn_degrees between 5 and 40 (20 works well); the spot must stay in view."}
        if not getattr(self.target.profile, "turns_camera", True):
            return {"ok": False, "error": f"{self.label} has no camera to turn ({self.target.profile.view}): nothing to calibrate."}
        if self.store.active():
            return {"ok": False, "error": "A run is playing: stop it first."}
        the = self.pilot
        cfg = the.cfg()
        if not self.brain.box_ok(cfg["model"]):
            return {"ok": False, "error": "Calibration needs Box questions: pick jev-1.14.0 or jev-preview in Settings → Play Tester."}
        t0 = time.perf_counter()
        spot = jev.new_ref("spot")

        def turn_and_look() -> dict[str, Any]:
            ready = the._ready_client()
            if not ready.get("ok"):
                return ready
            hwnd = ready["hwnd"]
            before = the.game.grab(hwnd, cfg["size"])
            img = before["image"]
            w, h = img.size
            crop = img.crop((int(w * 0.4), int(h * 0.35), int(w * 0.6), int(h * 0.65)))
            crop = screen.fit(crop, max(screen.MIN_SIDE + 20, crop.size[0] * 2))
            ctrl = the.game.controller(hwnd, threading.Event(), cfg["counts"])
            try:
                ctrl.look(turn)
                time.sleep(0.35)
                after = the.game.grab(hwnd, cfg["size"])
                qs = {
                    "same": {"type": "noul", "instructions": f"Does `frame` still show the place in this picture: {spot}?"},
                    "spot": {"type": "box", "instructions": f"the part of `frame` that shows what this picture shows: {spot}"},
                }
                res = the._ask(after, qs, cfg["model"], extra={spot: screen.jpeg(crop)})
                ctrl.look(-turn)
            except (ValueError, RuntimeError) as exc:
                return {"ok": False, "error": str(exc)}
            finally:
                ctrl.release_all()
            return {"ok": True, "res": res, "after": after}

        got = self._with_input(turn_and_look)
        if not got.get("ok"):
            return got
        res, after = got["res"], got["after"]
        if not res.get("ok"):
            return res
        same = jev.noul(res["answers"], "same")
        edges = jev.box(res["answers"], "spot")
        if same is None or same < 0.6 or not edges:
            return {"ok": False, "error": "Jev lost the spot after the turn: face something with clear detail and try a smaller turn_degrees."}
        moved = 0.5 - (edges["left"] + edges["right"]) / 2.0
        seen_deg = moved * float(getattr(self.target.profile, "hfov", policy.DEFAULTS["hfov"]))
        if abs(seen_deg) < 1.0 or (seen_deg > 0) != (turn > 0):
            return {"ok": False, "error": f"The view barely moved or moved the wrong way: check {self.label} has focus, then try again."}
        counts = int(min(100000, max(50, round(cfg["counts"] * turn / seen_deg))))
        self.save_setting("look_counts", str(counts))
        the.manual("calibrate", after, {}, [{"look": turn}, {"look": -turn}], [],
                   f"calibrate: turned {turn:g}°, saw {seen_deg:.1f}° -> {counts} counts per 90°", t0)
        return {"ok": True, "counts_per_90": counts, "was": int(cfg["counts"]), "asked_deg": turn,
                "seen_deg": round(seen_deg, 1), "confidence": edges.get("confidence"), "saved": True}

    # ------------------------------------------------------------------ positions and the level

    def where(self, target_actor: str = "", target_xyz: Any = "") -> dict[str, Any]:
        t = self.target
        if t.kind != "fortnite":
            reader = getattr(t, "where", None)
            if callable(reader):
                return reader(target_actor)
            return {"ok": False, "error": f"{self.label} doesn't report world positions: runs steer by the picture."}
        pose = self.pilot.pose()
        if not pose:
            state = self.beacon_state() or {}
            msg = ("The beacon printed, but not in the last few seconds: is the session still running?" if state.get("seen")
                   else "No beacon position yet: add the Play Tester beacon device to the island.")
            return {"ok": False, "error": msg, "beacon": state, "how": BEACON_HOW}
        out: dict[str, Any] = {"ok": True, "pose": {k: round(float(pose[k]), 1) for k in ("x", "y", "z", "yaw", "pitch")},
                               "age_s": pose["age_s"]}
        place = self.pilot.target_point(target_actor, target_xyz)
        if not place.get("ok"):
            return {**out, "ok": False, "error": place.get("error")}
        if place.get("point"):
            rel = geo.relate(pose, place["point"])
            out.update({"target_point": place["point"], "rel": rel, "say": geo.describe(rel)})
        return out

    def level_map(self, refresh: bool = False) -> dict[str, Any]:
        reader = getattr(self.target, "level_map", None)
        if not callable(reader):
            return {"ok": False, "error": f"{self.label} has no level map: only a Fortnite tester reads the island's devices."}
        out = reader(refresh)
        bcn = getattr(self.target, "beacon", None)
        try:
            out["printed_landmarks"] = [{"name": k, **{c: round(v[c], 1) for c in ("x", "y", "z")}} for k, v in bcn.marks().items()]
        except Exception:
            out["printed_landmarks"] = []
        return out

    # ------------------------------------------------------------------ runs

    def run(self, goal: str = "", target: str = "", success: str = "", interact: Any = False, max_steps: Any = 0,
            max_seconds: Any = 0, dry_run: Any = False, wait_s: Any = 20, origin: str = "tool",
            target_actor: str = "", target_xyz: Any = "", success_print: str = "", record: Any = False) -> dict[str, Any]:
        the = self.pilot
        started = the.start(goal=goal, target=target, success=success, interact=to_bool(interact),
                            max_steps=int(to_num(max_steps)), max_seconds=to_num(max_seconds), dry_run=to_bool(dry_run),
                            origin=origin, target_actor=target_actor, target_xyz=target_xyz, success_print=success_print,
                            record=to_bool(record))
        if not started.get("ok"):
            return {**started, "tester": self.id}
        started["tester"] = self.id
        wait = min(25.0, max(0.0, to_num(wait_s, 20)))
        if not wait:
            return started
        out = the.wait(started["run_id"], wait)
        out["tester"] = self.id
        if out.get("ok") and not out.get("finished"):
            out["next"] = f"Still playing: playtester_wait('{started['run_id']}', tester={self.id!r}) for the result, or playtester_stop."
        if started.get("note"):
            out["note"] = started["note"]
        return out

    def get_run(self, run_id: str = "", from_step: Any = 0) -> dict[str, Any]:
        rid = runs.clean_id(run_id)
        if not rid:
            latest = self.store.latest()
            rid = str(latest.get("id")) if latest else ""
        doc = self.store.get(rid) if rid else None
        if not doc:
            return {"ok": False, "error": "No play test has run yet." if not rid else f"No run {rid}."}
        first = int(to_num(from_step))
        steps = [s for s in doc.get("steps") or [] if int(s.get("n") or 0) > first]
        return {"ok": True, "tester": self.id, "run": {**{k: v for k, v in doc.items() if k != "steps"}, "steps": steps}}

    def list_runs(self, limit: Any = 10) -> dict[str, Any]:
        rows = self.store.summaries(int(min(50, max(1, to_num(limit, 10)))))
        return {"ok": True, "tester": self.id, "runs": rows, "count": len(rows)}

    def frame_bytes(self, run_id: str = "", step: Any = 0) -> tuple[str, int, bytes | None]:
        rid = runs.clean_id(run_id)
        if not rid:
            latest = self.store.latest()
            rid = str(latest.get("id")) if latest else ""
        doc = self.store.get(rid) if rid else None
        if not doc:
            return rid, 0, None
        n = int(to_num(step))
        if n <= 0:
            steps = doc.get("steps") or []
            n = int(steps[-1].get("n") or 0) if steps else 0
        if n <= 0:
            return rid, 0, None
        return rid, n, self.store.read_frame(rid, self.pilot.frame_slot(rid, n))

    def frame(self, run_id: str = "", step: Any = 0) -> dict[str, Any]:
        rid, n, data = self.frame_bytes(run_id, step)
        if data is None:
            return {"ok": False, "error": "No saved frame for that run and step."}
        path = self.store.frame_path(rid, self.pilot.frame_slot(rid, n))
        return {"ok": True, "tester": self.id, "run_id": rid, "step": n, "path": path,
                "files": [{"kind": "image", "path": path, "name": f"Play Tester {self.label} {rid} step {n}"}]}

    def rpc_frame(self, run_id: str = "", step: Any = 0, width: Any = 0) -> dict[str, Any]:
        rid, n, data = self.frame_bytes(run_id, step)
        if data is None:
            return {"ok": False, "error": "No saved frame."}
        return {"ok": True, "run_id": rid, "step": n, "src": screen.data_uri(data, int(to_num(width)))}

    def stop(self, run_id: str = "") -> dict[str, Any]:
        return {**self.pilot.stop(run_id), "tester": self.id}

    def wait(self, run_id: str = "", timeout_s: float = 20) -> dict[str, Any]:
        return {**self.pilot.wait(run_id, timeout_s), "tester": self.id}

    def lessons(self, target: str = "", limit: Any = 10) -> dict[str, Any]:
        return {**self.pilot.lessons(target, int(to_num(limit, 10))), "tester": self.id}

