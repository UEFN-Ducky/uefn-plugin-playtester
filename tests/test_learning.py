"""Run records, lessons, target memory and seeding the next run."""

from __future__ import annotations

import pytest

from uefn_plugin_playtester import learning, pilot, policy, runs

import test_geo as tg
import test_pilot as tp


def make(tmp_path, script=tp.walk_answers, data=None, island="Roguelike", game=None, **kw):
    store = runs.Runs(data or tp.FakeData(), str(tmp_path / "frames"))
    the = pilot.Pilot(store, tp.ScriptJev(script), game=game or tp.FakeGame(), push=lambda e: None,
                      settings=lambda: {"max_steps": "30"}, island=lambda: island, **kw)
    return the, store


def play(the, **kw):
    out = the.start(**kw)
    assert out["ok"], out
    return out, tp.wait_done(the, out["run_id"])


# ---------------------------------------------------------------- records


def test_steps_record_rule_model_questions_and_raw(tmp_path):
    the, store = make(tmp_path)
    _, run = play(the, goal="walk to the button", target="the button")
    s = run["steps"][0]
    assert s["rule"] == "approach" and s["model"] and "target" in s["q"] and s["raw"]["seen"]["noul"] == 0.93
    assert run["question_text"]["seen"].startswith("Is the button visible")
    assert run["island"] == "Roguelike" and run["steps"][-1]["rule"] == "reached"


def test_run_totals_add_up(tmp_path):
    the, store = make(tmp_path)
    _, run = play(the, goal="walk to the button", target="the button")
    t = run["totals"]
    assert t["first_seen"] == 1 and sum(t["rules"].values()) == run["n_steps"]
    assert t["rules"]["approach"] == run["n_steps"] - 1
    assert run["found"]["side"] == "right"


def test_focus_events_are_logged(tmp_path):
    class Away(tp.FakeGame):
        def status(self):
            return {**super().status(), "foreground": False}

        def focus(self, hwnd, allow_click=True):
            return {"ok": True, "method": "click-right", "ms": 40}

    the, store = make(tmp_path, game=Away())
    _, run = play(the, goal="walk to the button", target="the button")
    kinds = [e for e in run["events"] if e["kind"] == "focus"]
    assert kinds and kinds[0]["method"] == "click-right" and kinds[0]["ok"]
    les = store.data.get(f"lesson.{run['id']}")
    assert les["focus"]["click-right"] == len(kinds)


def test_focus_refused_fails_the_run_with_the_reason(tmp_path):
    class Stuck(tp.FakeGame):
        def status(self):
            return {**super().status(), "foreground": False}

        def focus(self, hwnd, allow_click=True):
            return {"ok": False, "method": "refused", "error": "Fortnite could not come to the front: click the Fortnite window once, then try again."}

    the, store = make(tmp_path, game=Stuck())
    _, run = play(the, goal="walk to the button", target="the button")
    assert run["status"] == "failed" and "click the Fortnite window" in run["reason"]
    assert store.data.get(f"lesson.{run['id']}")["outcome"] == "failed"


def test_cut_short_holds_become_events(tmp_path):
    class CutCtrl(tp.FakeCtrl):
        def run(self, actions, smooth=False):
            return [{"press": {**a, "cut_short": "Fortnite lost focus"}} for a in actions if a.get("keys")]

    class Game(tp.FakeGame):
        def controller(self, hwnd, stop, counts):
            self.ctrl = CutCtrl(stop)
            return self.ctrl

    the, store = make(tmp_path, game=Game())
    _, run = play(the, goal="walk to the button", target="the button")
    assert any(e["kind"] == "cut_short" for e in run["events"]) and run["totals"]["cut_short"] >= 1


# ---------------------------------------------------------------- lessons and memory


def test_every_finished_run_leaves_a_lesson_and_a_memory(tmp_path):
    the, store = make(tmp_path)
    _, run = play(the, goal="walk to the button", target="The Button")
    les = store.data.get(f"lesson.{run['id']}")
    assert les["outcome"] == "success" and les["target_key"] == "button" and les["island"] == "Roguelike"
    mem = store.data.get(learning.memory_key("Roguelike", "the button"))
    assert mem["tries"] == 1 and mem["wins"] == 1 and mem["last_side"] == "right" and mem["runs"] == [run["id"]]


def test_lessons_outlive_pruning(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "KEEP_RUNS", 2)
    monkeypatch.setattr(runs, "KEEP_FRAMES", 1)
    data = tp.FakeData()
    ids = []
    for _ in range(4):
        the, store = make(tmp_path, data=data)
        _, run = play(the, goal="walk to the button", target="the button")
        ids.append(run["id"])
    assert len(store.data.items("run.")) <= 3
    assert all(store.data.get(f"lesson.{i}") for i in ids)
    assert store.data.get(learning.memory_key("Roguelike", "the button"))["tries"] == 4


def test_fold_counts_blockers_and_keeps_newest_place():
    les = lambda **kw: {"id": kw.get("id", "r1"), "island": "I", "target": "t", "outcome": "success", "reason": "",
                        "steps": 10, "found": {"side": "left"}, "reached": {"x": 1, "y": 2, "z": 3}, "ended": 1, **kw}
    m = learning.fold(None, les())
    m = learning.fold(m, les(id="r2", outcome="blocked", reason="A menu covers the game: close it", reached=None, found={}))
    m = learning.fold(m, les(id="r3", reached={"x": 9, "y": 9, "z": 9}, found={"side": "right"}))
    assert (m["tries"], m["wins"]) == (3, 2) and m["blockers"] == {"menu": 1}
    assert m["reached"] == {"x": 9, "y": 9, "z": 9} and m["last_side"] == "right" and m["sides"] == {"left": 1, "right": 1}
    assert m["runs"] == ["r1", "r2", "r3"]


# ---------------------------------------------------------------- seeding


def test_seed_without_beacon_turns_toward_the_remembered_side():
    s = learning.seed({"tries": 2, "last_side": "left", "runs": ["r1"]}, beacon_on=False, has_point=False)
    assert s["scan_dir"] == -1 and "point" not in s


def test_seed_with_beacon_uses_where_it_was_reached():
    s = learning.seed({"tries": 1, "reached": {"x": 5, "y": 6, "z": 7}, "runs": ["r1"]}, beacon_on=True, has_point=False)
    assert s["point"] == {"x": 5.0, "y": 6.0, "z": 7.0} and s["point_from"] == "where it was reached"
    assert "point" not in learning.seed({"tries": 1, "reached": {"x": 5, "y": 6, "z": 7}}, beacon_on=True, has_point=True)


def test_seed_warns_after_a_blocked_run():
    s = learning.seed({"tries": 1, "last_outcome": "blocked", "last_reason": "A menu covers the game"}, beacon_on=False, has_point=False)
    assert "blocked" in s["warn"]


def test_second_run_scans_toward_remembered_side(tmp_path):
    data = tp.FakeData()
    data.put(learning.memory_key("Roguelike", "the button"),
             {"tries": 1, "wins": 1, "last_side": "left", "runs": ["r-old"], "island": "Roguelike", "target": "the button"})
    never = lambda i: {**tp.walk_answers(0), "seen": {"type": "noul", "noul": 0.05}}
    the, store = make(tmp_path, script=never, data=data)
    out, run = play(the, goal="find the button", target="the button", max_steps=3)
    assert out["memory_used"]["scan_side"] == "left"
    assert run["steps"][0]["rule"] == "scan" and run["steps"][0]["actions"] == [{"look": -60.0}]


def test_second_run_with_beacon_heads_for_the_remembered_place(tmp_path):
    data = tp.FakeData()
    data.put(learning.memory_key("Roguelike", "the button"),
             {"tries": 1, "wins": 1, "reached": {"x": 2000.0, "y": 0.0, "z": 0.0}, "runs": ["r-old"]})
    b = tg.FakeBeacon(yaw=0.0)
    the, store = make(tmp_path, script=tg.blind, data=data, game=tg.GeoGame(b), beacon=b)
    out, run = play(the, goal="walk to the button", target="the button")
    assert out["coordinates"] and out["memory_used"]["point_from"] == "where it was reached"
    assert run["status"] == "success" and run["steps"][0]["rule"].startswith("coord")


def test_lessons_tool_summarises_a_target(tmp_path):
    data = tp.FakeData()
    for _ in range(2):
        the, store = make(tmp_path, data=data)
        play(the, goal="walk to the button", target="the button")
    out = the.lessons("the button")
    assert out["ok"] and out["runs"] == 2 and out["wins"] == 2 and out["success_rate"] == 1.0
    assert out["recent"] and any("right" in tip for tip in out["tips"])
    every = the.lessons()
    assert every["runs"] == 2 and every["targets"][0]["wins"] == 2


def test_policy_scan_follows_scan_dir():
    m = policy.new_memory()
    m["scan_dir"] = -1
    d = policy.decide({"screen": {"choice": "playing", "p": 0.9}, "seen": 0.1}, m, {"target": "x"})
    assert d["actions"] == [{"look": -60.0}] and d["rule"] == "scan" and "left" in d["why"]


@pytest.mark.parametrize("text,want", [("The Vending Machine!", "vending-machine"), ("a  red button", "red-button"), ("", "")])
def test_target_keys(text, want):
    assert learning.key(text) == want
