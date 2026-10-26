"""Game profiles: questions name the game, keys and camera follow its controls, point and click clicks."""

from __future__ import annotations

import threading

import pytest

from uefn_plugin_playtester import inputs, policy, profiles, questions

from test_inputs import Rec


class PointRec(Rec):
    def move_to(self, x, y):
        self.events.append(("move_to", x, y))


def ctrl(profile, rec=None):
    p = profiles.make(profile) if isinstance(profile, str) else profile
    return inputs.Controller(7, rec or PointRec(), stop=threading.Event(), sleep=lambda s: None, keymap=p.keys,
                             look_mode=p.look, view=p.view, box=lambda: (100, 50, 900, 650))


def pressed(rec):
    return [e[1] for e in rec.events if e[0] == "key" and not e[2]]


S = inputs.SCAN


# ---------------------------------------------------------------- questions


def test_fortnite_questions_are_unchanged():
    kw = dict(goal="walk to the door", target="the door", success="Is the door open?", interact=True, has_previous=True)
    assert questions.step_questions(**kw) == questions.step_questions(**kw, profile=profiles.BUILTIN["fortnite"])
    q = questions.step_questions(**kw)
    assert "screenshot of Fortnite" in q["screen"]["instructions"] and "path" in q


def test_a_side_view_names_the_game_and_skips_the_walking_path():
    prof = profiles.make("side2d", name="Pixel Quest")
    q = questions.step_questions(goal="reach the flag", profile=prof)
    assert "screenshot of Pixel Quest" in q["screen"]["instructions"]
    assert "path" not in q and q["next"]["criteria"]["turn_left"].startswith("Face left")
    assert q["screen"]["criteria"]["playing"].startswith("The game in play")


def test_point_and_click_asks_where_to_click():
    q = questions.step_questions(goal="open the chest", target="", profile=profiles.make("pointclick"))
    assert set(q) == {"screen", "seen", "where", "target"} and "to click next" in q["target"]["instructions"]


# ---------------------------------------------------------------- profiles


def test_make_and_parse_keys():
    p = profiles.make("tps", name="Kart", view="side2d", keys="SPACE=ARROWUP, E=f")
    assert (p.view, p.look, p.name) == ("side2d", "facing", "Kart")
    assert p.keys["SPACE"] == "ARROWUP" and p.keys["E"] == "F" and p.keys["RIGHT"] == "D"
    assert profiles.from_dict(p.to_dict()) == p
    with pytest.raises(ValueError):
        profiles.make("chess")
    with pytest.raises(ValueError):
        profiles.parse_keys("SPACE=NOPE")


# ---------------------------------------------------------------- controls


def test_side_view_walks_the_way_the_player_faces():
    rec = PointRec()
    c = ctrl("side2d", rec)
    c.hold("W", 0.1)
    c.look(-40)
    out = c.hold("W", 0.1)
    assert pressed(rec) == [S["D"], S["A"]] and out["sent"] == ["A"]
    assert not any(e[0] == "move" for e in rec.events)  # no camera


def test_top_down_turns_pick_the_direction():
    rec = PointRec()
    c = ctrl("topdown", rec)
    c.look(90)
    c.hold("W", 0.1)
    c.look(45)
    c.hold("W", 0.1)
    assert pressed(rec) == [S["D"], S["S"], S["D"]]  # right, then down-right


def test_key_map_and_extended_arrow_keys():
    rec = PointRec()
    c = ctrl(profiles.make("side2d", keys="SPACE=ARROWUP"), rec)
    c.hold("SPACE", 0.1)
    assert pressed(rec) == [S["ARROWUP"]] and S["ARROWUP"] > 0xFF


def test_roblox_turns_with_the_right_button_held():
    rec = PointRec()
    c = ctrl("roblox", rec)
    out = c.look(30)
    flags = [e[1] for e in rec.events if e[0] == "button"]
    assert flags == [inputs.BUTTONS["right"][0], inputs.BUTTONS["right"][1]] and out["drag"] == "right"
    assert any(e[0] == "move" for e in rec.events)


def test_click_at_lands_inside_the_game():
    rec = PointRec()
    c = ctrl("pointclick", rec)
    done = c.run([{"click_at": [0.5, 0.25], "click": "left"}])
    assert ("move_to", 500, 200) in rec.events and done[0]["click_at"]["px"] == [500, 200]
    assert c.look(45)["skipped"] == "no camera"


def test_smooth_walk_in_a_side_view_swaps_the_held_key_when_turning():
    rec = PointRec()
    c = ctrl("side2d", rec)
    c.run([{"keys": ["W"], "seconds": 0.9}], smooth=True)
    c.run([{"look": -30.0}, {"keys": ["W"], "seconds": 0.9}], smooth=True)
    assert c.kept == ["A"] and [(e[1], e[2]) for e in rec.events if e[0] == "key"] == [(S["D"], False), (S["D"], True), (S["A"], False)]
    c.release_all()


# ---------------------------------------------------------------- point-and-click policy


def test_point_and_click_clicks_the_box_middle_and_succeeds():
    v = {"screen": {"choice": "playing", "p": 0.97}, "seen": 0.9,
         "box": {"left": 0.2, "right": 0.4, "top": 0.5, "bottom": 0.7, "confidence": 0.9}}
    d = policy.decide(v, None, {"target": "the start button", "view": "pointclick"})
    assert d["actions"] == [{"click_at": [0.3, 0.6], "click": "left"}] and d["finish"]["status"] == "success"


def test_point_and_click_waits_then_gives_up_when_nothing_shows():
    v = {"screen": {"choice": "playing", "p": 0.97}, "seen": 0.1}
    m = None
    for _ in range(int(policy.DEFAULTS["scan_turns"])):
        d = policy.decide(v, m, {"target": "the chest", "view": "pointclick"})
        m = d["memory"]
        assert d["rule"] == "click-wait"
    assert policy.decide(v, m, {"target": "the chest", "view": "pointclick"})["finish"]["status"] == "failed"


def test_point_and_click_with_a_check_settles_between_clicks():
    v = {"screen": {"choice": "playing", "p": 0.97}, "seen": 0.9, "done": 0.1,
         "box": {"left": 0.4, "right": 0.6, "top": 0.4, "bottom": 0.6, "confidence": 0.9}}
    goal = {"target": "the lever", "success": "Is the gate open?", "view": "pointclick"}
    first = policy.decide(v, None, goal)
    second = policy.decide(v, first["memory"], goal)
    assert first["rule"] == "click" and first["finish"] is None and second["rule"] == "settle"
