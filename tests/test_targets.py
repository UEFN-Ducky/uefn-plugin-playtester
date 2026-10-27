"""Targets: parse a target string, find windows, capture covered windows, Godot and browser senders."""

from __future__ import annotations

import threading
import time

import pytest
from PIL import Image

from uefn_plugin_playtester import capture, targets, windows
from uefn_plugin_playtester.targets import browser as web
from uefn_plugin_playtester.targets import godot, window

ROWS = [
    {"hwnd": 1, "title": "My Game", "exe": "MyGame.exe", "width": 1280, "height": 720, "minimized": False, "foreground": False},
    {"hwnd": 2, "title": "My Game - Launcher", "exe": "MyGame.exe", "width": 400, "height": 300, "minimized": False, "foreground": False},
    {"hwnd": 3, "title": "Inbox", "exe": "explorer.exe", "width": 900, "height": 700, "minimized": False, "foreground": True},
    {"hwnd": 4, "title": "", "exe": "x.exe", "width": 900, "height": 700, "minimized": False, "foreground": False},
    {"hwnd": 5, "title": "ShooterGame Preview [NetMode: Standalone 0]", "exe": "UnrealEditor.exe", "width": 1600,
     "height": 900, "minimized": False, "foreground": False},
    {"hwnd": 6, "title": "Roblox", "exe": "RobloxPlayerBeta.exe", "width": 1024, "height": 768, "minimized": False, "foreground": False},
]


# ---------------------------------------------------------------- parse


@pytest.mark.parametrize("text,spec", [
    ("", {"kind": "fortnite"}),
    ("fortnite", {"kind": "fortnite"}),
    ("godot", {"kind": "godot"}),
    ("godot:C:/Games/Platformer", {"kind": "godot", "project": "C:/Games/Platformer"}),
    ("unreal", {"kind": "unreal"}),
    ("unreal:ShooterGame-Win64-Shipping.exe", {"kind": "unreal", "exe": "ShooterGame-Win64-Shipping.exe"}),
    ("roblox", {"kind": "roblox"}),
    ("window:MyGame.exe", {"kind": "window", "exe": "MyGame.exe"}),
    ("window:My Game", {"kind": "window", "title": "My Game"}),
    ("web:localhost:8060", {"kind": "web", "url": "localhost:8060"}),
    ("https://example.com/game", {"kind": "web", "url": "https://example.com/game"}),
])
def test_parse_targets(text, spec):
    assert targets.parse(text) == spec


@pytest.mark.parametrize("bad", ["window:", "web:", "chess", {"kind": "chess"}])
def test_parse_refuses_what_it_cannot_play(bad):
    with pytest.raises(ValueError):
        targets.parse(bad)


def test_make_builds_each_kind():
    kinds = {k: type(targets.make(targets.parse(t))).__name__ for k, t in
             [("fortnite", "fortnite"), ("godot", "godot"), ("unreal", "unreal"), ("roblox", "roblox"),
              ("web", "web:localhost:1"), ("window", "window:a.exe")]}
    assert kinds == {"fortnite": "FortniteTarget", "godot": "GodotTarget", "unreal": "UnrealTarget",
                     "roblox": "RobloxTarget", "web": "BrowserTarget", "window": "WindowTarget"}
    assert targets.make({"kind": "roblox"}).profile.look == "rmb_drag"
    assert targets.make({"kind": "godot"}).needs_focus is False and targets.make({"kind": "window", "exe": "a.exe"}).needs_focus


# ---------------------------------------------------------------- windows


def test_pick_by_program_and_title_largest_first():
    assert [r["hwnd"] for r in windows.pick(ROWS, exe="mygame.EXE")] == [1, 2]
    assert [r["hwnd"] for r in windows.pick(ROWS, title="launcher")] == [2]
    assert windows.pick(ROWS) == []
    assert [r["hwnd"] for r in windows.games(ROWS)] == [1, 2, 6, 5]  # no shell, no untitled


def test_window_target_status_and_settle(monkeypatch):
    monkeypatch.setattr(windows, "list_windows", lambda: ROWS)
    t = targets.make({"kind": "window", "exe": "MyGame.exe", "settle_s": 0})
    st = t.status()
    assert st["ok"] and st["hwnd"] == 1 and t.label == "MyGame" and t.ready()["ok"]
    gone = targets.make({"kind": "window", "exe": "Nope.exe"}).status()
    assert not gone["ok"] and "Nope" in gone["error"]
    slow = targets.make({"kind": "window", "title": "My Game", "settle_s": 30})
    assert slow.ready(wait=False)["loading"]


def test_unreal_and_roblox_find_their_windows(monkeypatch):
    monkeypatch.setattr(windows, "list_windows", lambda: ROWS)
    assert targets.make({"kind": "unreal"}).status()["hwnd"] == 5
    assert targets.make({"kind": "roblox"}).status()["hwnd"] == 6


def test_window_controller_follows_the_profile():
    t = targets.make({"kind": "window", "exe": "a.exe", "profile": {"id": "side2d", "name": "Pixel"}})
    c = t.controller(7, threading.Event(), 2500)
    assert (c.look_mode, c.view, c.keymap["RIGHT"]) == ("facing", "side2d", "D") and t.profile.name == "Pixel"


# ---------------------------------------------------------------- capture


def test_capture_falls_back_on_black_timeout_and_errors():
    img = Image.new("RGB", (400, 300), (40, 90, 160))
    assert capture.window_image(1, grab=lambda h: img) is img
    assert capture.window_image(1, grab=lambda h: Image.new("RGB", (400, 300))) is None  # black
    assert capture.window_image(1, 0.05, grab=lambda h: time.sleep(0.5) or img) is None  # hung
    assert capture.window_image(1, grab=lambda h: (_ for _ in ()).throw(OSError("gone"))) is None


def test_a_covered_window_is_captured_from_behind(monkeypatch):
    img = Image.new("RGB", (1280, 720), (40, 90, 160))
    monkeypatch.setattr(window.screen, "foreground", lambda: 99)
    monkeypatch.setattr(window.capture, "window_image", lambda hwnd: img)
    frame = targets.make({"kind": "window", "exe": "a.exe"}).grab(1, 640)
    assert frame["covered"] and frame["width"] == 640 and frame["jpeg"][:2] == b"\xff\xd8"


# ---------------------------------------------------------------- Godot


class FakeGodot:
    def __init__(self, shot):
        self.calls, self.shot = [], shot

    def call_tool(self, name, args):
        assert name == "godot_call"
        tool, a = args["tool"], args["arguments"]
        self.calls.append((tool, a))
        if tool == "get_runtime_info":
            return {"ok": True, "result": {"current_scene": "Main"}}
        if tool == "get_runtime_screenshot":
            return {"ok": True, "result": {"path": self.shot}}
        if tool == "evaluate_runtime_expression":
            value = "(1152, 648)" if "size" in a["expression"] else ("(1, 2, 3)" if a["node_path"].endswith("Player") else "(4, 6, 3)")
            return {"ok": True, "result": {"value": value}}
        return {"ok": True}


def test_godot_frames_input_and_positions(tmp_path):
    shot = tmp_path / "frame.jpg"
    Image.new("RGB", (1152, 648), (30, 120, 60)).save(shot)
    api = FakeGodot(str(shot))
    t = targets.make({"kind": "godot", "project": "C:/Games/Platformer", "player": "/root/Main/Player",
                      "profile": {"id": "side2d"}}, api=api)
    assert t.label == "Godot · Platformer" and t.status()["ok"]
    assert t.grab(0, 512)["width"] == 512
    c = t.controller(0, threading.Event(), 2500)
    c.hold("SPACE", 0.05)
    c.hold("W", 0.05)
    c.run([{"click_at": [0.5, 0.5], "click": "left"}])
    events = [a["event"] for tool, a in api.calls if tool == "simulate_runtime_input_event"]
    keys = [(e["keycode"], e["pressed"]) for e in events if e["type"] == "key"]
    assert keys == [(32, True), (32, False), (ord("D"), True), (ord("D"), False)]
    clicks = [e for e in events if e["type"] == "mouse_button"]
    assert [c_["pressed"] for c_ in clicks] == [True, False] and clicks[0]["position"] == {"x": 576.0, "y": 324.0}
    where = t.where("/root/Main/Door")
    assert where["player"] == [1.0, 2.0, 3.0] and where["distance"] == 5.0


def test_godot_says_how_to_start_the_probe():
    class Down:
        def call_tool(self, name, args):
            raise ValueError("no running game")

    st = targets.make({"kind": "godot"}, api=Down()).status()
    assert not st["ok"] and "install_runtime_probe" in st["error"]
    assert st["title"] == "The Godot game isn't running" and "godot_call" not in st["fix"], "the tab gets plain words"
    assert godot.numbers({"x": 1, "y": 2}) == [1.0, 2.0] and godot.keycode("ARROWUP") == 4194320


def test_a_godot_error_wrapped_in_json_reads_as_words():
    nested = ('{"error": "{\\n  \\"ok\\": false,\\n  \\"state\\": \\"waiting_for_editor\\",\\n  '
              '\\"error\\": \\"no Godot editor with the MCP addon is connected\\"\\n}"}')
    assert godot.reason(nested) == ("no Godot editor with the MCP addon is connected", "waiting_for_editor")
    out = godot.trouble(nested)
    assert out["title"] == "The Godot editor isn't open" and "{" not in out["title"] + out["fix"]
    assert "install_runtime_probe" in out["error"], "agents still get the steps"


def test_a_missing_window_says_what_to_do_in_two_short_lines(monkeypatch):
    monkeypatch.setattr(windows, "list_windows", lambda: [])
    st = targets.make({"kind": "roblox"}).status()
    assert st["title"] == "Roblox isn't open" and st["fix"] == "Start Roblox and join a game."
    assert "RobloxPlayerBeta.exe" in st["error"]


def test_a_preset_games_window_opens_that_preset():
    assert targets.for_window({"exe": "FortniteClient-Win64-Shipping.exe"}) == ("fortnite", "fortnite")
    assert targets.for_window({"exe": "RobloxPlayerBeta.exe"}) == ("roblox", "roblox")
    assert targets.for_window({"exe": "MyGame.exe", "title": "My Game"}) == ("window:MyGame.exe", "window")


# ---------------------------------------------------------------- browser


def test_browser_target_opens_a_tab_and_sends_dom_input(monkeypatch):
    sent = []

    def fake_op(op, pane="", args=None):
        sent.append((op, pane, dict(args or {})))
        if op == "metrics":
            return {"ok": True, "width": 800.0, "height": 600.0}
        if op == "alive":
            return {"ok": True, "alive": True}
        return {"ok": True}

    monkeypatch.setattr(web.appcall, "run", lambda method, local, params, timeout_s=30.0: fake_op(**params))

    class Api:
        def call_tool(self, name, args):
            assert name == "browser_new_tab" and args == {"url": "localhost:8060"}
            return {"ok": True, "pane_id": "browser::p2"}

    t = targets.make(targets.parse("web:localhost:8060"), api=Api())
    assert t.open() == {"ok": True, "pane": "browser::p2"} and t.spec["pane"] == "browser::p2"
    assert t.status()["ok"] and t.label == "Web · localhost:8060"
    c = t.controller(0, threading.Event(), 2500)
    c.hold("W", 0.05)          # side view: W walks right
    c.run([{"click_at": [0.25, 0.5], "click": "left"}])
    keys = [(a["name"], a["up"]) for op, _p, a in sent if op == "key"]
    assert keys == [("D", False), ("D", True)]
    mouse = [(a["type"], a.get("x"), a.get("y")) for op, _p, a in sent if op == "mouse"]
    assert mouse == [("mouseMoved", 200, 300), ("mousePressed", 200.0, 300.0), ("mouseReleased", 200.0, 300.0)]
    assert all(p == "browser::p2" for _op, p, _a in sent)
