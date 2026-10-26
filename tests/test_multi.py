"""Several testers at once, end to end: real runs, the real input lease, fake games and a scripted Jev."""

from __future__ import annotations

import os
import threading
import time

from uefn_plugin_playtester import lease, runs, testers
from uefn_plugin_playtester import tester as one

import test_pilot as tp

NEVER = lambda i: {**tp.walk_answers(0), "seen": {"type": "noul", "noul": 0.05}}  # never found: plays to the limit


class Clock:
    """Who held the keyboard when: (tester, start, end) per step's presses."""

    def __init__(self):
        self.spans, self.lock = [], threading.Lock()

    def add(self, who, start, end):
        with self.lock:
            self.spans.append((who, start, end))


class Ctrl(tp.FakeCtrl):
    def __init__(self, stop, who, clock):
        super().__init__(stop)
        self.who, self.clock, self.kept = who, clock, []

    def run(self, actions, smooth=False):
        start = time.perf_counter()
        out = super().run(actions)
        time.sleep(0.01)
        self.clock.add(self.who, start, time.perf_counter())
        return out


class Game(tp.FakeGame):
    def __init__(self, who, clock, needs_focus=True):
        super().__init__()
        self.who, self.clock, self.needs_focus, self.label, self.kind = who, clock, needs_focus, who, "window"

    def controller(self, hwnd, stop, counts):
        self.ctrl = Ctrl(stop, self.who, self.clock)
        return self.ctrl


def make(tmp_path, who, game, the_lease):
    data = testers.ScopedData(tp.FakeData(), f"t.{who}.")
    store = runs.Runs(data, str(tmp_path / f"frames-{who}"))
    return one.Tester(who, {"id": who, "label": who}, store=store, target=game, settings=lambda: {"max_steps": "30"},
                  save_setting=lambda k, v: None, lease=the_lease, client=tp.ScriptJev(NEVER))


def overlaps(a, b):
    return a[1] < b[2] and b[1] < a[2]


def three(tmp_path):
    clock = Clock()
    the_lease = lease.InputLease(str(tmp_path / "lease"))
    games = {"desk-a": Game("desk-a", clock), "desk-b": Game("desk-b", clock), "web": Game("web", clock, needs_focus=False)}
    return clock, {who: make(tmp_path, who, g, the_lease) for who, g in games.items()}


def test_desktop_testers_take_turns_and_the_web_one_runs_free(tmp_path):
    clock, ts = three(tmp_path)
    started = {who: t.run(goal="find the chest", target="the chest", wait_s=0) for who, t in ts.items()}
    assert all(s["ok"] for s in started.values()), started
    done = {who: tp.wait_done(t.pilot, started[who]["run_id"], timeout=60) for who, t in ts.items()}
    assert all(r["status"] == "timeout" and r["n_steps"] == 30 for r in done.values()), {w: r["status"] for w, r in done.items()}
    desk = [s for s in clock.spans if s[0] != "web"]
    web = [s for s in clock.spans if s[0] == "web"]
    assert len(desk) == 60 and len(web) == 30
    clashes = [(a, b) for i, a in enumerate(desk) for b in desk[i + 1:] if a[0] != b[0] and overlaps(a, b)]
    assert clashes == []  # one keyboard: never two desktop testers pressing at once
    assert any(overlaps(w, d) for w in web for d in desk)  # the web tester didn't wait for them
    for t in ts.values():  # every tester gave the lease back
        assert not t.lease.holds(t.id)


def test_each_tester_keeps_its_own_runs_and_stop_stops_only_one(tmp_path):
    clock, ts = three(tmp_path)
    started = {who: t.run(goal="find the chest", target="the chest", wait_s=0) for who, t in ts.items()}
    time.sleep(0.3)
    assert ts["desk-b"].stop()["stopped"]
    done = {who: tp.wait_done(t.pilot, started[who]["run_id"], timeout=60) for who, t in ts.items()}
    assert done["desk-b"]["status"] == "stopped" and done["desk-b"]["n_steps"] < 30, done["desk-b"]
    assert done["desk-a"]["status"] == done["web"]["status"] == "timeout"
    for who, t in ts.items():
        rid = started[who]["run_id"]
        assert [r["id"] for r in t.list_runs()["runs"]] == [rid]  # nobody else's runs
        assert f"frames-{who}" in t.store.frame_path(rid, 1)
        if done[who]["n_steps"]:  # a run stopped before its first step has no frame
            assert os.path.isfile(t.store.frame_path(rid, 1))
        assert t.get_run(rid)["run"]["tester"] == who
