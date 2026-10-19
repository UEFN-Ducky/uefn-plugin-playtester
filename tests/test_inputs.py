"""Keyboard and mouse with a recording sender instead of SendInput."""

from __future__ import annotations

import threading

import pytest

from uefn_plugin_playtester import inputs


class Rec:
    def __init__(self, hwnd=7, lose_after=None):
        self.events = []
        self.hwnd = hwnd
        self.calls = 0
        self.lose_after = lose_after

    def key(self, scan, up):
        self.events.append(("key", scan, up))

    def move(self, dx, dy):
        self.events.append(("move", dx, dy))

    def button(self, flag):
        self.events.append(("button", flag))

    def foreground(self):
        self.calls += 1
        if self.lose_after is not None and self.calls > self.lose_after:
            return 0
        return self.hwnd


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += max(s, 0.001)


def ctrl(rec, **kw):
    c = Clock()
    return inputs.Controller(7, rec, clock=c.now, sleep=c.sleep, **kw)


def test_key_names_and_aliases():
    assert inputs.key_names("w+a") == ["W", "A"]
    assert inputs.key_names(["jump", "use"]) == ["SPACE", "E"]
    assert inputs.key_names("") == []
    with pytest.raises(ValueError):
        inputs.key_names("F4")


def test_hold_two_keys_downs_then_ups():
    rec = Rec()
    out = ctrl(rec).hold("W+A", 0.6)
    keys = [e for e in rec.events if e[0] == "key"]
    assert keys == [("key", 0x11, False), ("key", 0x1E, False), ("key", 0x1E, True), ("key", 0x11, True)]
    assert out["keys"] == ["W", "A"] and out["released"] and 0.55 <= out["seconds"] <= 0.7


def test_hold_releases_even_when_something_breaks():
    rec = Rec()
    c = ctrl(rec)
    calls = {"n": 0}

    def boom(s):
        calls["n"] += 1
        raise RuntimeError("boom")

    c._sleep = boom
    with pytest.raises(RuntimeError):
        c.hold("W", 1.0)
    assert ("key", 0x11, True) in rec.events and not c._down


def test_hold_stops_when_focus_is_lost():
    rec = Rec(lose_after=3)
    out = ctrl(rec).hold("W", 2.0)
    assert out["cut_short"] == "Fortnite lost focus"
    assert rec.events[-1] == ("key", 0x11, True)


def test_nothing_pressed_without_focus():
    rec = Rec(lose_after=0)
    with pytest.raises(RuntimeError):
        ctrl(rec).hold("W", 0.5)
    assert rec.events == []


def test_look_converts_degrees_and_slices():
    rec = Rec()
    out = ctrl(rec, counts_per_90=2500).look(45)
    moves = [e for e in rec.events if e[0] == "move"]
    assert sum(m[1] for m in moves) == 1250 and out["dx_counts"] == 1250
    assert len(moves) == out["slices"] > 1


def test_click_presses_and_releases():
    rec = Rec()
    out = ctrl(rec).click("left", 0.1)
    assert rec.events[0] == ("button", 0x0002) and rec.events[-1] == ("button", 0x0004)
    assert out["button"] == "left"
    with pytest.raises(ValueError):
        ctrl(Rec()).click("middle")


def test_run_does_actions_in_order_and_honours_stop():
    rec = Rec()
    stop = threading.Event()
    c = ctrl(rec, stop=stop)
    done = c.run([{"look": 10}, {"keys": ["W"], "seconds": 0.2}, {"wait": 0.1}])
    assert [list(d)[0] for d in done] == ["look", "press", "wait"]
    stop.set()
    assert c.run([{"keys": ["W"], "seconds": 0.2}]) == []
