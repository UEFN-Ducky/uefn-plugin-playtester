"""The run engine end to end with a fake game, a scripted Jev and an in-memory data store."""

from __future__ import annotations

import io
import json
import threading
import time

from PIL import Image

from uefn_plugin_playtester import jev, pilot, runs


class FakeData:
    def __init__(self):
        self.docs = {}
        self.lock = threading.Lock()

    def get(self, key, default=None):
        with self.lock:
            raw = self.docs.get(key)
        return json.loads(raw) if raw is not None else default

    def put(self, key, value):
        with self.lock:
            self.docs[key] = json.dumps(value)
        return {"ok": True}

    def items(self, prefix):
        with self.lock:
            return {k: json.loads(v) for k, v in self.docs.items() if k.startswith(prefix)}

    def delete(self, key):
        with self.lock:
            self.docs.pop(key, None)


class FakeCtrl:
    def __init__(self, stop):
        self.stop = stop
        self.ran = []
        self.released = 0

    def run(self, actions, smooth=False):
        if self.stop.is_set():
            return []
        self.ran.append(actions)
        time.sleep(0.01)
        return [{"press": a} for a in actions]

    def release_all(self):
        self.released += 1


class FakeGame:
    def __init__(self):
        self.ctrl = None
        img = Image.new("RGB", (640, 360))
        buf = io.BytesIO()
        img.save(buf, "JPEG")
        self.img, self.data = img, buf.getvalue()

    def status(self):
        return {"ok": True, "hwnd": 7, "foreground": True, "gameplay_loaded": True, "loaded_for_s": 60}

    def ready(self, wait=True):
        return self.status()

    def focus(self, hwnd, allow_click=True):
        return {"ok": True, "method": "plain", "ms": 1}

    def grab(self, hwnd, size):
        return {"image": self.img, "jpeg": self.data, "width": 640, "height": 360, "ms": 3}

    def smaller(self, image):
        return self.data

    def thumb(self, data):
        return "data:image/jpeg;base64,AA"

    def draw_box(self, data, edges, label=""):
        return data

    def controller(self, hwnd, stop, counts):
        self.ctrl = FakeCtrl(stop)
        return self.ctrl


def walk_answers(i):
    """Target starts right and small, gets centered and big."""
    cx = max(0.5, 0.75 - 0.08 * i)
    h = min(0.6, 0.12 + 0.1 * i)
    return {
        "screen": {"type": "choice", "choice": "playing", "probabilities": {"playing": 0.97}},
        "path": {"type": "choice", "choice": "ahead", "probabilities": {"ahead": 0.8}},
        "seen": {"type": "noul", "noul": 0.93},
        "where": {"type": "choice", "choice": "center", "probabilities": {"center": 0.7}},
        "target": {"type": "box", "box": {"left": cx - 0.05, "right": cx + 0.05, "top": 0.3, "bottom": 0.3 + h}, "confidence": 0.9},
        "moved": {"type": "noul", "noul": 0.9},
    }


class ScriptJev:
    def __init__(self, script=walk_answers, fail=False, delay=0.0):
        self._key = "k"
        self.no_box = set()
        self.calls = []
        self.script = script
        self.fail = fail
        self.delay = delay

    def box_ok(self, model):
        return True

    def decide(self, state, questions, images=None, model=""):
        self.calls.append({"state": state, "questions": questions, "images": list((images or {}).keys())})
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            return {"ok": False, "status": 500, "error": "Jev error 500: down", "ms": 5}
        return {"ok": True, "answers": self.script(len(self.calls) - 1), "usage": {"input_tokens": 100, "output_tokens": 5}, "ms": 12}


def make(tmp_path, **kw):
    store = runs.Runs(FakeData(), str(tmp_path / "frames"))
    events = []
    game = FakeGame()
    the = pilot.Pilot(store, kw.pop("client", None) or ScriptJev(), game=game, push=events.append,
                      settings=lambda: {"max_steps": "30"})
    return the, store, game, events


def wait_done(the, rid, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        run = the.runs.get(rid)
        if run and run["status"] in runs.DONE:
            return run
        time.sleep(0.02)
    raise AssertionError("run never finished")


def test_run_walks_to_the_target_and_succeeds(tmp_path):
    the, store, game, events = make(tmp_path)
    out = the.start(goal="walk to the button", target="the button")
    assert out["ok"], out
    run = wait_done(the, out["run_id"])
    assert run["status"] == "success" and run["reason"] == "Reached the button."
    assert run["n_steps"] >= 3 and run["avg_ms"] >= 0 and run["jev_ms"] == 12
    assert run["tokens"]["input"] == 100 * run["n_steps"]
    first = run["steps"][0]
    assert any("look" in a for a in first["actions"]) and first["view"]["box"]
    assert game.ctrl.ran and game.ctrl.released >= 1
    assert store.read_frame(run["id"], 1)
    assert store.active() is None
    time.sleep(0.1)
    kinds = [e["type"] for e in events]
    assert "playtester_step" in kinds and kinds.count("playtester_run") >= 2
    # questions carry the frame and, after step 1, the previous frame
    calls = the.jev.calls
    assert "previous" not in calls[0]["state"] and "previous" in calls[1]["state"]
    assert "target" in calls[0]["questions"] and calls[0]["questions"]["target"]["type"] == "box"


def test_wait_returns_the_result_with_moves(tmp_path):
    the, *_ = make(tmp_path)
    rid = the.start(goal="walk to the button", target="the button")["run_id"]
    res = the.wait(rid, 10)
    assert res["ok"] and res["finished"] and res["status"] == "success"
    assert res["moves"] and res["moves"][0].startswith("#1 turn right")
    assert res["frame"].endswith("001.jpg") or res["frame"].endswith(f"{res['n_steps']:03d}.jpg")


def test_stop_from_another_process_stops_after_the_step(tmp_path):
    never = lambda i: {**walk_answers(0), "seen": {"type": "noul", "noul": 0.1}}
    the, store, game, _ = make(tmp_path, client=ScriptJev(never, delay=0.05))
    rid = the.start(goal="find the button", target="the button")["run_id"]
    time.sleep(0.2)
    store.request_stop(rid)  # what the tab does from the app process
    run = wait_done(the, rid)
    assert run["status"] == "stopped" and game.ctrl.released >= 1


def test_only_one_run_at_a_time(tmp_path):
    never = lambda i: {**walk_answers(0), "seen": {"type": "noul", "noul": 0.1}}
    the, *_ = make(tmp_path, client=ScriptJev(never, delay=0.05))
    first = the.start(goal="find the button", target="the button")
    second = the.start(goal="something else")
    assert first["ok"] and not second["ok"] and first["run_id"] in second["error"]
    assert the.stop()["stopped"]
    wait_done(the, first["run_id"])


def test_dry_run_never_presses(tmp_path):
    the, store, game, _ = make(tmp_path)
    rid = the.start(goal="walk to the button", target="the button", dry_run=True)["run_id"]
    run = wait_done(the, rid)
    assert run["status"] == "success" and game.ctrl.ran == []
    assert all(s["dry_run"] and s["done"] == [] for s in run["steps"])


def test_jev_errors_three_times_fail_the_run(tmp_path):
    the, *_ = make(tmp_path, client=ScriptJev(fail=True))
    rid = the.start(goal="walk to the button", target="the button")["run_id"]
    run = wait_done(the, rid)
    assert run["status"] == "failed" and "500" in run["reason"] and run["n_steps"] == 3


def test_step_limit_ends_free_play_as_done(tmp_path):
    free = lambda i: {**walk_answers(0), "next": {"type": "choice", "choice": "forward", "probabilities": {"forward": 0.8}}}
    the, *_ = make(tmp_path, client=ScriptJev(free))
    rid = the.start(goal="look around the spawn", max_steps=4)["run_id"]
    run = wait_done(the, rid)
    assert run["status"] == "done" and run["n_steps"] == 4


def test_start_needs_a_goal_and_a_key(tmp_path, monkeypatch):
    the, *_ = make(tmp_path)
    assert not the.start()["ok"]
    client = ScriptJev()
    client._key = ""
    the2, *_ = make(tmp_path, client=client)
    monkeypatch.setattr(jev, "api_key", lambda: "")
    out = the2.start(goal="walk")
    assert not out["ok"] and out["needs_key"]


def test_manual_actions_keep_a_short_history(tmp_path):
    the, store, game, events = make(tmp_path)
    frame = game.grab(7, 640)
    for _ in range(45):
        the.manual("press", frame, {}, [{"keys": ["W"], "seconds": 0.3}], [], "press W 0.3 s", time.perf_counter())
    doc = store.get("manual")
    assert doc["n_steps"] == 45 and len(doc["steps"]) == pilot.MANUAL_KEEP
    assert store.read_frame("manual", the.frame_slot("manual", 45))
