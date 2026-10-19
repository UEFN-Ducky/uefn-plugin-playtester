"""Smooth walking, the hold watchdog, Verse prints (positions, landmarks, events), the map and elevation."""

from __future__ import annotations

import threading
import time

from uefn_plugin_playtester import beacon, geo, inputs, pilot, policy, runs

import test_geo as tg
import test_pilot as tp
from test_inputs import Rec

W, D, SPACE = inputs.SCAN["W"], inputs.SCAN["D"], inputs.SCAN["SPACE"]


def keys(rec):
    return [(e[1], e[2]) for e in rec.events if e[0] == "key"]


def ctrl(rec=None, gap=5.0, stop=None):
    return inputs.Controller(7, rec or Rec(), gap=gap, stop=stop or threading.Event(), sleep=lambda s: None)


# ---------------------------------------------------------------- smooth walking


def test_smooth_walk_holds_w_across_steps_and_turns_while_walking():
    rec = Rec()
    c = ctrl(rec)
    for turn in (14.0, 6.0, -3.0, 0.0, 9.0):
        acts = ([{"look": turn}] if turn else []) + [{"keys": ["W"], "seconds": 0.9}]
        done = c.run(acts, smooth=True)
        assert done[-1]["keep"]["held"]
    assert keys(rec) == [(W, False)]  # one press, still held
    assert sum(1 for e in rec.events if e[0] == "move") > 0
    c.release_all()
    assert keys(rec)[-1] == (W, True)


def test_a_step_without_a_walk_stops_first():
    rec = Rec()
    c = ctrl(rec)
    c.run([{"keys": ["W"], "seconds": 0.9}], smooth=True)
    c.run([{"keys": ["E"], "seconds": 0.1}], smooth=True)
    assert keys(rec) == [(W, False), (W, True), (inputs.SCAN["E"], False), (inputs.SCAN["E"], True)]
    assert c.drain_events()[0]["why"] == "stop for the next move"


def test_sharp_turn_stops_then_turns_then_walks_again():
    rec = Rec()
    c = ctrl(rec)
    c.run([{"keys": ["W"], "seconds": 0.9}], smooth=True)
    c.run([{"look": 120.0}, {"keys": ["W"], "seconds": 0.9}], smooth=True)
    k = keys(rec)
    assert k == [(W, False), (W, True), (W, False)] and c.kept == ["W"]
    c.release_all()


def test_jump_while_walking_keeps_w_down():
    rec = Rec()
    c = ctrl(rec)
    c.run([{"keys": ["W"], "seconds": 0.9}], smooth=True)
    c.run([{"keys": ["SPACE"], "seconds": 0.1}, {"keys": ["W"], "seconds": 0.9}], smooth=True)
    assert keys(rec) == [(W, False), (SPACE, False), (SPACE, True)] and c.kept == ["W"]
    c.release_all()


def test_timed_and_strafe_moves_are_not_kept():
    rec = Rec()
    c = ctrl(rec)
    c.run([{"keys": ["W"], "seconds": 0.25, "timed": True}], smooth=True)
    c.run([{"keys": ["W", "D"], "seconds": 0.6}], smooth=True)
    assert c.kept == [] and keys(rec)[-1] in ((W, True), (D, True))


def test_not_smooth_is_unchanged():
    rec = Rec()
    c = ctrl(rec)
    c.run([{"keys": ["W"], "seconds": 0.3}])
    assert keys(rec) == [(W, False), (W, True)] and c.kept == []


# ---------------------------------------------------------------- watchdog


def wait_until(cond, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_watchdog_lets_go_when_no_decision_arrives():
    rec = Rec()
    c = inputs.Controller(7, rec, gap=0.2)
    c.keep(["W"])
    assert wait_until(lambda: c.kept == [])
    assert keys(rec)[-1] == (W, True) and "no decision" in c.drain_events()[0]["why"]


def test_watchdog_lets_go_when_focus_is_lost():
    rec = Rec()
    c = inputs.Controller(7, rec, gap=5)
    c.keep(["W"])
    rec.hwnd = 99  # another window came to the front
    assert wait_until(lambda: c.kept == [])
    assert c.drain_events()[0]["why"] == "Fortnite lost focus"


def test_watchdog_lets_go_on_stop():
    rec, stop = Rec(), threading.Event()
    c = inputs.Controller(7, rec, gap=5, stop=stop)
    c.keep(["W"])
    stop.set()
    assert wait_until(lambda: c.kept == [])


# ---------------------------------------------------------------- prints


LINES = [
    "[2026.10.18-18.00.00:100][  5]LogVerse: : [PTPOS] 1 0 100 -20 245 87.3 -4.1 ground",
    "[2026.10.18-18.00.00:200][  5]LogVerse: : [PTMARK] Red Door 1200 50 0",
    "[2026.10.18-18.00.00:300][  5]LogVerse: : [PTEVENT] coin collected",
    "[2026.10.18-18.00.00:400][  5]LogVerse: Display: Player reached checkpoint 2",
    "[2026.10.18-18.00.00:500][  6]LogNet: not a print",
]


def test_parse_state_mark_and_prints(tmp_path):
    log = tmp_path / "UnrealEditorFortnite.log"
    log.write_text("\n".join(LINES) + "\n", encoding="utf-8")
    t0 = beacon.parse(LINES[0])["t"]
    b = beacon.Beacon(str(log), clock=lambda: t0 + 1)
    assert b.pose()["state"] == "ground"
    assert b.marks()["Red Door"]["x"] == 1200.0
    texts = [p["text"] for p in b.prints_since(t0)]
    assert texts == ["[PTEVENT] coin collected", "Player reached checkpoint 2"]
    assert [p["text"] for p in b.prints_since(t0, "COIN")] == ["[PTEVENT] coin collected"]
    assert b.prints_since(t0 + 60) == []


def test_old_position_lines_without_state_still_parse():
    p = beacon.parse("[2026.10.18-18.00.00:100][  5]LogVerse: : [PTPOS] 1 0 100 -20 245 87.3 -4.1")
    assert p and "state" not in p


class PrintingBeacon(tg.FakeBeacon):
    def __init__(self, at_step=3, **kw):
        super().__init__(**kw)
        self.n, self.at = 0, at_step
        self.marks_ = {"Red Door": {"x": 600.0, "y": 300.0, "z": 0.0, "t": 0}}

    def fresh(self):
        self.n += 1
        return super().fresh()

    def prints_since(self, since):
        return [{"t": since + 1, "text": "[PTEVENT] coin collected"}] if self.n >= self.at else []

    def marks(self):
        return dict(self.marks_)


def test_run_passes_on_a_verse_print(tmp_path):
    b = PrintingBeacon(at_step=3)
    store = runs.Runs(tp.FakeData(), str(tmp_path / "f"))
    never = lambda i: {**tp.walk_answers(0), "seen": {"type": "noul", "noul": 0.05}}
    the = pilot.Pilot(store, tp.ScriptJev(never), game=tp.FakeGame(), push=lambda e: None, settings=lambda: {}, beacon=b)
    out = the.start(goal="collect the coin", target="the coin", success_print="coin collected")
    run = tp.wait_done(the, out["run_id"])
    assert run["status"] == "success" and run["reason"] == "Verse printed: [PTEVENT] coin collected"
    assert any(e["kind"] == "print" for e in run["events"])


def test_reaching_the_target_waits_for_the_print():
    v = {"screen": {"choice": "playing", "p": 0.97}, "seen": 0.95,
         "box": {"left": 0.4, "right": 0.6, "top": 0.2, "bottom": 0.8, "confidence": 0.9}}
    d = policy.decide(v, None, {"target": "the coin", "success": "coin collected"})
    assert d["finish"] is None and d["rule"] == "wait-success"


# ---------------------------------------------------------------- map, landmarks, elevation


def test_nearby_landmarks_and_text():
    me = {"x": 0, "y": 0, "z": 0, "yaw": 0}
    rows = geo.nearby(me, [{"name": "Tower", "x": 1200, "y": 0, "z": 450}, {"name": "Shop", "x": 0, "y": -600, "z": 0},
                           {"name": "Far", "x": 99999, "y": 0, "z": 0}], 3)
    assert [r["name"] for r in rows] == ["Shop", "Tower"]
    assert geo.nearby_text(rows) == "Shop 6 m left; Tower 12 m ahead, 4.5 m up"


class Climber(tg.MovingCtrl):
    def run(self, actions, smooth=False):
        self.smooth_seen = getattr(self, "smooth_seen", []) + [smooth]
        out = super().run(actions)
        if any(a.get("keys") == ["W"] for a in actions):
            self.b.p["z"] += 60.0
        return out


def test_steps_record_climb_state_and_nearby_and_jev_hears_them(tmp_path):
    b = PrintingBeacon(at_step=999, yaw=0.0)
    b.p["state"] = "ground"

    class Game(tp.FakeGame):
        def controller(self, hwnd, stop, counts):
            self.ctrl = Climber(stop, b, counts)
            return self.ctrl

    game = Game()
    store = runs.Runs(tp.FakeData(), str(tmp_path / "f"))
    jev = tp.ScriptJev(tg.blind)
    the = pilot.Pilot(store, jev, game=game, push=lambda e: None, settings=lambda: {"max_steps": "4"}, beacon=b,
                      landmarks=lambda: [{"name": "Shop", "x": 300.0, "y": -500.0, "z": 0.0, "source": "level"}],
                      locate=lambda l: {"ok": True, "point": {"x": 3000.0, "y": 0.0, "z": 600.0}, "label": l})
    out = the.start(goal="go up to the button", target="the button", target_actor="Button")
    run = tp.wait_done(the, out["run_id"])
    second = run["steps"][1]
    assert second["where"]["climb_m"] == 0.6 and second["where"]["pose"]["state"] == "ground"
    assert {n["name"] for n in second["where"]["nearby"]} == {"Shop", "Red Door"}
    state = jev.calls[1]["state"]
    assert "climbing 0.6 m" in state["player"] and "Shop" in state["nearby"]
    assert all(game.ctrl.smooth_seen)  # smooth walking is the default


def test_floor_climb_keeps_walking():
    rel = geo.relate({"x": 0, "y": 0, "z": 0, "yaw": 0}, {"x": 400, "y": 0, "z": 450})
    v = {"screen": {"choice": "playing", "p": 0.97}, "seen": 0.1, "geo": rel, "climb_m": 0.6, "stairs_seen": 0.1}
    d = policy.decide(v, None, {"target": "the button"})
    assert d["rule"] == "floor-climb" and d["actions"] == [{"keys": ["W"], "seconds": 0.9}]


def test_dry_run_is_never_smooth(tmp_path):
    the, store, _game, _events = tp.make(tmp_path)
    out = the.start(goal="walk to the button", target="the button", dry_run=True)
    run = tp.wait_done(the, out["run_id"])
    assert run["smooth"] is False
