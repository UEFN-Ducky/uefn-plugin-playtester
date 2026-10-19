"""Decision rules: every branch from a made-up view of a frame."""

from __future__ import annotations

from uefn_plugin_playtester import policy

PLAYING = {"choice": "playing", "p": 0.97}


def view(**kw):
    v = {"screen": PLAYING, "path": {"choice": "ahead", "p": 0.8}}
    v.update(kw)
    return v


def box(cx, h, top=0.3):
    w = 0.1
    return {"left": cx - w / 2, "right": cx + w / 2, "top": top, "bottom": top + h, "confidence": 0.9}


GOAL = {"target": "the button", "success": "", "interact": False}


def test_steer_right_and_walk_when_far():
    d = policy.decide(view(seen=0.93, box=box(0.71, 0.2)), None, GOAL)
    looks = [a["look"] for a in d["actions"] if "look" in a]
    walks = [a for a in d["actions"] if a.get("keys") == ["W"]]
    assert looks and looks[0] > 0  # turn right
    assert abs(looks[0] - round(0.21 * 80 * 0.85, 1)) < 0.2
    assert walks and 0.3 <= walks[0]["seconds"] <= 1.5
    assert "right of center" in d["why"] and d["finish"] is None


def test_straight_ahead_walks_without_turning():
    d = policy.decide(view(seen=0.9, box=box(0.52, 0.1)), None, GOAL)
    assert not any("look" in a for a in d["actions"])
    assert d["actions"][-1]["keys"] == ["W"]


def test_reached_target_without_success_check_finishes():
    d = policy.decide(view(seen=0.95, box=box(0.5, 0.5)), None, GOAL)
    assert d["finish"]["status"] == "success"


def test_interact_holds_e_when_prompt_is_up():
    goal = {**GOAL, "interact": True}
    d = policy.decide(view(seen=0.95, box=box(0.5, 0.5), prompt=0.9), None, goal)
    assert {"keys": ["E"], "seconds": 0.7} in d["actions"]
    assert d["finish"]["status"] == "success"


def test_interact_nudges_then_tries_e_anyway():
    goal = {**GOAL, "interact": True, "success": "Is the door open?"}
    m = None
    for _ in range(3):
        d = policy.decide(view(seen=0.95, box=box(0.5, 0.5), prompt=0.1, done=0.1), m, goal)
        m = d["memory"]
        assert d["actions"][-1]["keys"] == ["W"]
    d = policy.decide(view(seen=0.95, box=box(0.5, 0.5), prompt=0.1, done=0.1), m, goal)
    assert d["actions"][-1]["keys"] == ["E"] and d["finish"] is None


def test_after_using_it_waits_for_the_success_check():
    goal = {**GOAL, "interact": True, "success": "Is an item in the hotbar?"}
    d = policy.decide(view(seen=0.95, box=box(0.5, 0.5), prompt=0.9, done=0.1), None, goal)
    assert d["actions"][-1]["keys"] == ["E"]
    for i in (1, 2):
        d = policy.decide(view(seen=0.95, box=box(0.5, 0.5), prompt=0.9, done=0.1), d["memory"], goal)
        assert d["actions"][-1] == {"wait": 0.6} and f"({i}/2)" in d["why"]
    d = policy.decide(view(seen=0.95, box=box(0.5, 0.5), prompt=0.9, done=0.1), d["memory"], goal)
    assert d["actions"][-1]["keys"] == ["E"]  # tries again
    d = policy.decide(view(seen=0.95, box=box(0.5, 0.5), prompt=0.9, done=0.95), d["memory"], goal)
    d = policy.decide(view(seen=0.95, box=box(0.5, 0.5), prompt=0.9, done=0.95), d["memory"], goal)
    assert d["finish"]["status"] == "success"


def test_scan_when_not_seen_then_explore():
    m = None
    for i in range(6):
        d = policy.decide(view(seen=0.1), m, GOAL)
        m = d["memory"]
        assert d["actions"] == [{"look": 60.0}], i
    d = policy.decide(view(seen=0.1), m, GOAL)
    assert d["actions"] == [{"keys": ["W"], "seconds": 1.2}]
    assert "full look around" in d["why"]


def test_lost_target_turns_back_toward_last_side():
    d = policy.decide(view(seen=0.9, box=box(0.2, 0.2)), None, GOAL)
    d = policy.decide(view(seen=0.1), d["memory"], GOAL)
    assert d["actions"] == [{"look": -30.0}]


def test_stuck_after_two_walks_without_movement():
    m = policy.new_memory()
    m["last"] = "walk"
    d = policy.decide(view(seen=0.9, box=box(0.5, 0.1), moved=0.1), m, GOAL)
    assert d["actions"][-1]["keys"] == ["W"]
    d = policy.decide(view(seen=0.9, box=box(0.5, 0.1), moved=0.1), d["memory"], GOAL)
    assert d["actions"][0] == {"keys": ["SPACE"], "seconds": 0.1}
    assert d["actions"][1]["keys"] == ["W", "D"]
    assert "stuck" in d["why"]


def test_menu_blocks_and_loading_waits():
    d = policy.decide(view(screen={"choice": "menu", "p": 0.9}), None, GOAL)
    assert d["finish"]["status"] == "blocked"
    d = policy.decide(view(screen={"choice": "loading", "p": 0.9}), None, GOAL)
    assert d["actions"] == [{"wait": 1.0}] and d["finish"] is None
    d = policy.decide(view(screen={"choice": "eliminated", "p": 0.9}), None, GOAL)
    assert d["finish"]["status"] == "failed"
    d = policy.decide(view(screen={"choice": "menu", "p": 0.4}, seen=0.9, box=box(0.5, 0.1)), None, GOAL)
    assert d["finish"] is None  # unsure about the menu: keep playing


def test_success_needs_two_passes():
    goal = {**GOAL, "success": "Is the door open?"}
    d = policy.decide(view(done=0.9), None, goal)
    assert d["finish"] is None and d["actions"] == [{"wait": 0.3}]
    d = policy.decide(view(done=0.9), d["memory"], goal)
    assert d["finish"]["status"] == "success"
    d = policy.decide(view(done=0.9), None, goal)
    d = policy.decide(view(done=0.2, seen=0.1), d["memory"], goal)
    assert d["memory"]["done"] == 0 and d["finish"] is None


def test_no_box_uses_where_and_near():
    d = policy.decide(view(seen=0.9, where={"choice": "left", "p": 0.8}, near=0.2), None, GOAL)
    assert d["actions"][0]["look"] < 0 and d["actions"][-1]["keys"] == ["W"]
    d = policy.decide(view(seen=0.9, where={"choice": "center", "p": 0.8}, near=0.9), None, GOAL)
    assert d["finish"]["status"] == "success"


def test_free_goal_follows_jev_and_avoids_walls():
    goal = {"target": "", "success": "", "interact": False}
    d = policy.decide(view(next={"choice": "turn_left", "p": 0.7}), None, goal)
    assert d["actions"] == [{"look": -45.0}]
    d = policy.decide(view(next={"choice": "forward", "p": 0.7}, path={"choice": "right", "p": 0.8}), None, goal)
    assert d["actions"][0] == {"look": 50.0}
    d = policy.decide(view(next={"choice": "forward", "p": 0.2}), None, goal)
    assert d["actions"] == [{"keys": ["W"], "seconds": 1.2}]


def test_every_decision_says_why():
    goal = {**GOAL, "success": "Is it done?", "interact": True}
    for v in (view(), view(seen=0.9, box=box(0.8, 0.1)), view(seen=0.1), view(done=0.9),
              view(screen={"choice": "popup", "p": 0.8})):
        assert policy.decide(v, None, goal)["why"]
