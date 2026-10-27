"""Testers: open, find again from another process, keep their own runs and brain, close; the tools route by tester."""

from __future__ import annotations

import os
import threading
import time

import pytest

import uefn_plugin_playtester as plugin
from uefn_plugin_playtester import lease, testers, windows

import test_pilot as tp

GAME = {"hwnd": 11, "title": "Pixel Quest", "exe": "PixelQuest.exe", "width": 1280, "height": 720,
        "minimized": False, "foreground": True}


class FakeApi:
    """The plugin host: data shared like the real store (one dict for every process), folders, tools."""

    def __init__(self, tmp, data=None):
        self.data = data or tp.FakeData()
        self.tmp = tmp
        self.tools, self.rpcs, self.nodes = {}, {}, {}

    def local_dir(self, name=""):
        path = os.path.join(str(self.tmp), name or "root")
        os.makedirs(path, exist_ok=True)
        return path

    def call_tool(self, name, args):
        if name == "browser_new_tab":
            return {"ok": True, "pane_id": "browser::7"}
        raise ValueError(f"no tool {name}")

    def tool(self, intent="", listener=False):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco

    def register_panel_rpc(self, name, fn):
        self.rpcs[name] = fn

    def register_pipeline_node(self, name):
        def deco(fn):
            self.nodes[name] = fn
            return fn
        return deco

    def log(self, msg):
        pass


def manager(api, **kw):
    return testers.TesterManager(api, settings=lambda: {"brain": "typesafe"}, save_setting=lambda k, v: None, **kw)


@pytest.fixture(autouse=True)
def one_window(monkeypatch):
    monkeypatch.setattr(windows, "list_windows", lambda: [GAME])


def test_the_default_tester_is_fortnite_on_the_old_keys(tmp_path):
    api = FakeApi(tmp_path)
    api.data.put("run.r-old", {"id": "r-old"})
    api.data.put("sum.r-old", {"id": "r-old", "started": 1})
    m = manager(api)
    t = m.pick()
    assert t.id == "default" and t.target.kind == "fortnite" and t.store.get("r-old")
    assert [x.id for x in m.list()] == ["default"]


def test_open_a_window_tester_with_its_own_runs_and_profile(tmp_path):
    api = FakeApi(tmp_path)
    m = manager(api)
    t = m.open("window:PixelQuest.exe", profile="side2d")
    assert t.id == "pixelquest" and t.target.profile.view == "side2d" and t.target.profile.name == "PixelQuest"
    t.store.save({"id": "r-1", "started": 2, "status": "success"})
    assert api.data.get("t.pixelquest.run.r-1") and m.pick().store.get("r-1") is None  # default doesn't see it
    again = m.open("window:PixelQuest.exe")
    assert again.id == "pixelquest-2"
    with pytest.raises(ValueError):
        m.open("fortnite")
    with pytest.raises(ValueError):
        m.open("window:")


def test_another_process_sees_the_same_testers(tmp_path):
    api = FakeApi(tmp_path)
    m1 = manager(api)
    opened = m1.open("window:PixelQuest.exe", label="Pixel")
    m2 = manager(FakeApi(tmp_path, data=api.data))  # the agents' tool server
    assert [t.id for t in m2.list()] == ["default", opened.id] and m2.get(opened.id).label == "Pixel"
    assert m2.close(opened.id)["ok"]
    assert [t.id for t in m1.list()] == ["default"]


def test_close_forgets_runs_and_refuses_the_default(tmp_path):
    api = FakeApi(tmp_path)
    m = manager(api)
    t = m.open("window:PixelQuest.exe")
    t.store.save({"id": "r-9", "started": 1, "status": "done"})
    t.store.save_frame("r-9", 1, b"jpg")
    assert m.close(t.id)["ok"] and api.data.get("t.pixelquest.run.r-9") is None
    assert not os.path.exists(os.path.join(str(tmp_path), "frames-pixelquest", "r-9"))
    assert not m.close("default")["ok"] and not m.close("nope")["ok"]


def test_pick_remembers_the_chat_and_names_open_testers(tmp_path):
    m = manager(FakeApi(tmp_path))
    t = m.open("window:PixelQuest.exe")
    assert m.pick(t.id, chat="c1").id == t.id and m.pick("", chat="c1").id == t.id and m.pick("", chat="c2").id == "default"
    with pytest.raises(LookupError, match="pixelquest"):
        m.pick("ghost")


def test_who_answers_is_per_tester(tmp_path):
    saved = {}
    m = testers.TesterManager(FakeApi(tmp_path), settings=lambda: {"brain": "typesafe"},
                              save_setting=lambda k, v: saved.__setitem__(k, v))
    t = m.open("window:PixelQuest.exe")
    assert t.set_brain("openai:gpt-6-luna")["pick"] == "openai:gpt-6-luna"
    assert t.settings()["brain"] == "gateway" and m.pick().settings()["brain"] == "typesafe"
    assert m.get(t.id).doc["brain"]["gateway_model"] == "gpt-6-luna"  # saved, survives a rebuild
    m.pick().set_brain("uefn_ducky:jev-1.14.0")
    assert saved == {"brain": "ducky_ai", "model": "jev-1.14.0"}  # the default writes Settings
    assert not t.set_brain("")["ok"]


def test_a_tester_keeps_its_own_mouse_speed(tmp_path):
    saved = {}
    m = testers.TesterManager(FakeApi(tmp_path), settings=lambda: {"look_counts": "2500"},
                              save_setting=lambda k, v: saved.__setitem__(k, v))
    t = m.open("window:PixelQuest.exe")
    t.save_setting("look_counts", "900")
    assert t.pilot.cfg()["counts"] == 900.0 and m.pick().pilot.cfg()["counts"] == 2500.0 and saved == {}


def test_web_tester_opens_its_browser_tab(tmp_path):
    m = manager(FakeApi(tmp_path))
    t = m.open("web:localhost:8060")
    assert t.target.pane == "browser::7" and m.get(t.id).doc["spec"]["pane"] == "browser::7"


# ---------------------------------------------------------------- the tools


def registered(tmp_path):
    api = FakeApi(tmp_path)
    plugin.register(api)
    return api


def test_tools_route_by_tester_and_old_calls_still_use_fortnite(tmp_path):
    api = registered(tmp_path)
    opened = api.tools["playtester_open"](target="window:PixelQuest.exe", profile="side2d")
    tid = opened["tester"]["id"]
    assert opened["ok"] and tid in opened["use"]
    st = api.tools["playtester_status"](tester=tid)
    assert st["tester"]["id"] == tid and st["target"]["kind"] == "window" and {t["id"] for t in st["testers"]} == {"default", tid}
    assert api.tools["playtester_status"]()["tester"]["id"] == "default"
    bad = api.tools["playtester_run"](goal="x", tester="ghost")
    assert not bad["ok"] and "ghost" in bad["error"]
    assert not api.tools["playtester_map"](tester=tid)["ok"]  # only Fortnite reads a level
    assert api.tools["playtester_close"](tester=tid)["ok"]
    assert [t["id"] for t in api.tools["playtester_testers"]()["testers"]] == ["default"]


def test_targets_tool_lists_presets_and_windows(tmp_path):
    out = registered(tmp_path).tools["playtester_targets"]()
    assert [p["kind"] for p in out["presets"]] == ["fortnite", "godot", "unreal", "roblox", "web"]
    assert out["windows"][0]["exe"] == "PixelQuest.exe"


def test_a_game_window_with_a_tester_points_at_its_tab(tmp_path, monkeypatch):
    rows = [{"hwnd": 9, "title": "Fortnite", "exe": "FortniteClient-Win64-Shipping.exe", "width": 1920, "height": 1080},
            {"hwnd": 8, "title": "Pixel Quest", "exe": "PixelQuest.exe", "width": 1280, "height": 720}]
    monkeypatch.setattr(windows, "list_windows", lambda: rows)
    api = registered(tmp_path)
    tid = api.tools["playtester_open"](target="window:PixelQuest.exe")["tester"]["id"]
    got = {w["exe"]: w for w in api.rpcs["targets"](thumbnails=False)["windows"]}
    assert (got["FortniteClient-Win64-Shipping.exe"]["target"], got["FortniteClient-Win64-Shipping.exe"]["tester"]) == ("fortnite", "default")
    assert got["PixelQuest.exe"]["tester"] == tid


def test_web_op_only_drives_play_tester_tabs(tmp_path):
    api = registered(tmp_path)
    out = api.rpcs["web_op"](op="capture", pane="browser::gmail")
    assert not out["ok"] and "isn't a Play Tester tab" in out["error"]


def test_every_panel_call_takes_a_tester(tmp_path):
    import inspect

    api = registered(tmp_path)
    for name, fn in api.rpcs.items():
        if name in ("testers", "targets", "open", "web_op"):
            continue
        assert "tester" in inspect.signature(fn).parameters, name


def test_workflow_nodes_open_once_and_pick_their_tester(tmp_path):
    api = registered(tmp_path)
    ctx = {"config": {"target": "window:PixelQuest.exe", "label": "Pixel", "profile": "side2d"}}
    first = api.nodes["playtester.open"](ctx)
    again = api.nodes["playtester.open"](ctx)
    assert first["ok"] and first["tester"] == again["tester"] == "pixel"
    st = api.nodes["playtester.status"]({"inputs": {"tester": "pixel"}, "config": {}})
    assert st["tester"]["id"] == "pixel" and "next" in st
    assert api.nodes["playtester.stop"]({"config": {"tester": "pixel"}})["tester"] == "pixel"
    assert not api.nodes["playtester.run"]({"config": {"tester": "ghost", "goal": "x"}})["ok"]


# ---------------------------------------------------------------- the lease


def test_lease_hands_over_only_when_someone_waits(tmp_path):
    a = lease.InputLease(str(tmp_path))
    b = lease.InputLease(str(tmp_path))  # another process
    released = []
    with a.turn("one", before_handover=lambda: released.append("one")):
        pass
    assert a.holds("one") and released == []  # alone: keeps it, keys stay down
    got = []
    th = threading.Thread(target=lambda: (b.acquire("two"), got.append(True)))
    th.start()
    time.sleep(0.2)
    assert b.waiting("two") and not got
    with a.turn("one", before_handover=lambda: released.append("one")):
        pass
    th.join(2)
    assert got == [True] and released == ["one"] and not a.holds("one") and b.holds("two")
    b.release("two")


def test_two_testers_trade_the_lease_without_stalling(tmp_path):
    """Both just handed over and both see the other waiting: the courtesy must not hold both back."""
    order = []

    def play(who, inst):
        for _ in range(20):
            with inst.turn(who):
                order.append(who)
                time.sleep(0.005)
        inst.release(who)

    started = time.perf_counter()
    ths = [threading.Thread(target=play, args=(w, lease.InputLease(str(tmp_path)))) for w in ("a", "b")]
    for th in ths:
        th.start()
    for th in ths:
        th.join(20)
    assert len(order) == 40 and time.perf_counter() - started < 4.0  # 40 turns; the stall took 0.5 s each
    swaps = sum(1 for x, y in zip(order, order[1:]) if x != y)
    assert swaps >= 15  # they really alternate


def test_lease_wait_stops_on_stop(tmp_path):
    a = lease.InputLease(str(tmp_path))
    a.acquire("one")
    stop = threading.Event()
    stop.set()
    with pytest.raises(lease.Stopped):
        lease.InputLease(str(tmp_path)).acquire("two", stop)
    a.release("one")


def test_no_lease_for_targets_that_take_input_in_the_background(tmp_path):
    a = lease.InputLease(str(tmp_path))
    a.acquire("desktop")
    done = []
    with a.turn("godot", needed=False):
        done.append(True)
    assert done == [True]
    a.release("desktop")
