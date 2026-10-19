"""World coordinates: maths, the beacon's log lines, the coordinate rules and the engine."""

from __future__ import annotations

import time

from uefn_plugin_playtester import beacon, geo, pilot, policy, questions, runs

import test_pilot as tp


def pose(x=0.0, y=0.0, z=0.0, yaw=0.0, tick=1):
    return {"x": x, "y": y, "z": z, "yaw": yaw, "pitch": 0.0, "tick": tick, "age_s": 0.2}


# ---------------------------------------------------------------- maths


def test_relate_ahead_right_behind_and_floors():
    me = pose(yaw=0.0)
    ahead = geo.relate(me, {"x": 1000, "y": 0, "z": 0})
    assert ahead["side"] == "ahead" and ahead["distance_m"] == 10.0 and ahead["turn_deg"] == 0.0
    right = geo.relate(me, {"x": 0, "y": 1000, "z": 0})
    assert right["side"] == "right" and right["turn_deg"] == 90.0
    behind = geo.relate(pose(yaw=90.0), {"x": -100, "y": -1000, "z": 0})
    assert behind["side"] == "behind" and abs(behind["turn_deg"]) > 110
    up = geo.relate(me, {"x": 300, "y": 0, "z": 420})
    assert up["floor"] == "up" and up["height_m"] == 4.2 and not up["arrived"]
    there = geo.relate(me, {"x": 150, "y": 50, "z": 60})
    assert there["arrived"]


def test_wrap_and_point_and_describe():
    assert geo.wrap(190) == -170 and geo.wrap(-190) == 170 and geo.wrap(180) == 180
    assert geo.point("1,2,3") == {"x": 1.0, "y": 2.0, "z": 3.0}
    assert geo.point({"X": 1, "Y": 2, "Z": 3}) == {"x": 1.0, "y": 2.0, "z": 3.0}
    assert geo.point([1, 2]) is None and geo.point("one two") is None
    text = geo.describe(geo.relate(pose(yaw=0.0), {"x": -2000, "y": 600, "z": 450}))
    assert "behind you" in text and "m up (another floor)" in text


# ---------------------------------------------------------------- beacon log


LOG_LINES = [
    "[2026.10.18-18.00.00:100][  5]LogVerse: : [PTPOS] 1 0 100.5 -20 245 87.3 -4.1",
    "[2026.10.18-18.00.00:300][  6]LogNet: something else",
    "[2026.10.18-18.00.00:350][  7]LogVerse: : [PTPOS] 2 0 160.25 -20 245 92 -4",
    "[2026.10.18-18.00.00:360][  7]LogVerse: : [PTPOS] 2 1 9 9 9 0 0",
]


def test_parse_line():
    p = beacon.parse(LOG_LINES[0])
    assert p["tick"] == 1 and p["player"] == 0 and p["x"] == 100.5 and p["yaw"] == 87.3
    assert beacon.parse(LOG_LINES[1]) is None


def test_beacon_reads_only_new_lines(tmp_path):
    log = tmp_path / "UnrealEditorFortnite.log"
    log.write_text("\n".join(LOG_LINES[:2]) + "\n", encoding="utf-8")
    stamp = beacon.parse(LOG_LINES[2])["t"]
    b = beacon.Beacon(str(log), clock=lambda: stamp + 0.5)
    assert b.pose()["tick"] == 1
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(LOG_LINES[2] + "\n" + LOG_LINES[3][:30])  # a half-written last line
    p = b.pose()
    assert p["tick"] == 2 and p["x"] == 160.25 and p["age_s"] == 0.5
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(LOG_LINES[3][30:] + "\n")
    assert b.pose(1)["x"] == 9.0
    old = beacon.Beacon(str(log), clock=lambda: stamp + 60)
    assert old.pose() and old.fresh() is None


def test_no_log_means_no_pose(tmp_path):
    assert beacon.Beacon(str(tmp_path / "missing.log")).pose() is None


# ---------------------------------------------------------------- rules


GOAL = {"target": "the button", "success": "", "interact": False}


def view(**kw):
    v = {"screen": {"choice": "playing", "p": 0.97}, "path": {"choice": "ahead", "p": 0.8}, "seen": 0.1}
    v.update(kw)
    return v


def rel_to(x, y, z=0.0, yaw=0.0):
    return geo.relate(pose(yaw=yaw), {"x": x, "y": y, "z": z})


def test_behind_turns_first_without_walking():
    d = policy.decide(view(geo=rel_to(-3000, 800)), None, GOAL)
    assert len(d["actions"]) == 1 and abs(d["actions"][0]["look"]) == 120.0
    assert "behind you" in d["why"]


def test_facing_it_walks_and_corrects():
    d = policy.decide(view(geo=rel_to(3000, 900)), None, GOAL)  # about 17° right
    assert d["actions"][0]["look"] > 0 and d["actions"][-1]["keys"] == ["W"]
    d = policy.decide(view(geo=rel_to(3000, 500)), None, GOAL)  # under 12°: just walk
    assert d["actions"] == [{"keys": ["W"], "seconds": 1.5}]
    assert "m away" in d["why"]


def test_building_in_the_way_goes_around():
    d = policy.decide(view(geo=rel_to(3000, 0), path={"choice": "left", "p": 0.8}), None, GOAL)
    assert d["actions"][0] == {"look": -50.0} and d["actions"][1]["keys"] == ["W"]
    assert "building is in the way" in d["why"]


def test_upstairs_heads_for_the_stairs_or_looks_for_them():
    up = rel_to(400, 0, 450)
    d = policy.decide(view(geo=up, stairs_seen=0.9, stairs_box={"left": 0.7, "right": 0.9, "top": 0.4, "bottom": 0.8, "confidence": 0.8}), None, GOAL)
    assert d["actions"][0]["look"] > 0 and d["actions"][-1]["keys"] == ["W"] and "way up" in d["why"]
    d = policy.decide(view(geo=up, stairs_seen=0.1), None, GOAL)
    assert d["actions"] == [{"look": 60.0}] and "stairs" in d["why"]
    down = rel_to(300, 0, -500)
    assert "down" in policy.decide(view(geo=down, stairs_seen=0.1), None, GOAL)["why"]


def test_far_target_on_another_floor_walks_across_first():
    d = policy.decide(view(geo=rel_to(5000, 0, 450)), None, GOAL)
    assert d["actions"][-1]["keys"] == ["W"]


def test_arrived_by_coordinates_finishes_or_uses():
    d = policy.decide(view(geo=rel_to(120, 0, 30)), None, GOAL)
    assert d["finish"]["status"] == "success"
    d = policy.decide(view(geo=rel_to(120, 0, 30), prompt=0.9), None, {**GOAL, "interact": True})
    assert {"keys": ["E"], "seconds": 0.7} in d["actions"]


def test_visible_and_near_hands_over_to_the_box():
    box = {"left": 0.6, "right": 0.7, "top": 0.3, "bottom": 0.5, "confidence": 0.9}
    d = policy.decide(view(seen=0.95, box=box, geo=rel_to(800, 100)), None, GOAL)
    assert "right of center" in d["why"]  # the vision branch, not the coordinate one


def test_real_movement_drives_stuck_detection():
    m = policy.new_memory()
    m["last"] = "walk"
    d = policy.decide(view(geo=rel_to(3000, 0), moved_m=0.05), m, GOAL)
    d = policy.decide(view(geo=rel_to(3000, 0), moved_m=0.02, moved=0.99), d["memory"], GOAL)
    assert "stuck" in d["why"]


def test_floor_questions_only_when_on_another_floor():
    q = questions.step_questions(goal="g", target="t", floor="up")
    assert q["stairs_seen"]["type"] == "noul" and q["stairs"]["type"] == "box"
    assert "stairs_seen" not in questions.step_questions(goal="g", target="t", floor="same")


# ---------------------------------------------------------------- engine


class FakeBeacon:
    """The player walks toward +X each step and turns as asked, like the game would."""

    def __init__(self, yaw=180.0, counts_true=5000.0):
        self.p = pose(yaw=yaw, tick=0)
        self.counts_true = counts_true

    def fresh(self):
        self.p = {**self.p, "tick": self.p["tick"] + 1}
        return dict(self.p)

    def pose(self):
        return dict(self.p)


class MovingCtrl(tp.FakeCtrl):
    def __init__(self, stop, b, counts):
        super().__init__(stop)
        self.b = b
        self.counts_per_90 = counts

    def run(self, actions, smooth=False):
        if self.stop.is_set():
            return []
        out = []
        for a in actions:
            if a.get("look"):
                counts = a["look"] / 90.0 * self.counts_per_90
                real = counts / self.b.counts_true * 90.0  # the game turns by its own sensitivity
                self.b.p["yaw"] = geo.wrap(self.b.p["yaw"] + real)
                out.append({"look": {"dx_deg": a["look"]}})
            elif a.get("keys") == ["W"]:
                import math

                yaw = math.radians(self.b.p["yaw"])
                self.b.p["x"] += math.cos(yaw) * 400 * a["seconds"]
                self.b.p["y"] += math.sin(yaw) * 400 * a["seconds"]
                out.append({"press": a})
            else:
                out.append({"press": a})
        return out


class GeoGame(tp.FakeGame):
    def __init__(self, b):
        super().__init__()
        self.b = b

    def controller(self, hwnd, stop, counts):
        self.ctrl = MovingCtrl(stop, self.b, counts)
        return self.ctrl


def blind(i):
    a = tp.walk_answers(0)
    a["seen"] = {"type": "noul", "noul": 0.05}
    a.pop("target", None)
    return a


def test_run_steers_by_coordinates_and_learns_the_mouse(tmp_path):
    b = FakeBeacon(yaw=180.0, counts_true=5000.0)  # facing away from the target; the mouse is half as fast as set
    saved = {}
    store = runs.Runs(tp.FakeData(), str(tmp_path / "frames"))
    the = pilot.Pilot(store, tp.ScriptJev(blind), game=GeoGame(b), push=lambda e: None,
                      settings=lambda: {"look_counts": "2500", "max_steps": "60"},
                      beacon=b, locate=lambda label: {"ok": True, "point": {"x": 2000.0, "y": 0.0, "z": 0.0}, "label": label},
                      save_setting=lambda k, v: saved.__setitem__(k, v))
    out = the.start(goal="walk to the button", target="the button", target_actor="Button_1")
    assert out["ok"] and out["coordinates"]
    run = tp.wait_done(the, out["run_id"], 20)
    assert run["status"] == "success", (run["reason"], [s["why"] for s in run["steps"]])
    first = run["steps"][0]
    assert first["where"]["rel"]["side"] == "behind" and "behind you" in first["why"]
    assert any(s.get("where", {}).get("calib") for s in run["steps"])
    assert int(saved.get("look_counts", 0)) > 3500  # learned it needs more counts per 90°
    assert the.jev.calls[0]["state"]["target_place"].startswith("20 m away, behind you")


def test_run_without_beacon_says_so(tmp_path):
    store = runs.Runs(tp.FakeData(), str(tmp_path / "frames"))
    the = pilot.Pilot(store, tp.ScriptJev(), game=tp.FakeGame(), push=lambda e: None, settings=lambda: {},
                      beacon=None, locate=lambda label: {"ok": True, "point": {"x": 1.0, "y": 2.0, "z": 3.0}})
    out = the.start(goal="walk to the button", target="the button", target_actor="Button_1")
    assert out["ok"] and not out["coordinates"] and "beacon" in out["note"]
    tp.wait_done(the, out["run_id"])


def test_bad_target_label_stops_before_starting(tmp_path):
    store = runs.Runs(tp.FakeData(), str(tmp_path / "frames"))
    the = pilot.Pilot(store, tp.ScriptJev(), game=tp.FakeGame(), push=lambda e: None,
                      locate=lambda label: {"ok": False, "error": "No actor labelled 'x' in the level"})
    out = the.start(goal="go", target_actor="x")
    assert not out["ok"] and "No actor" in out["error"] and store.active() is None
    assert not the.start(goal="go", target_xyz="1,2")["ok"]
