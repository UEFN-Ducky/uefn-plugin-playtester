"""Brain routing: who answers (TypeSafe key, Ducky AI, gateway) and the gateway's JSON answers."""

from __future__ import annotations

from uefn_plugin_playtester import brains, jev

Q = {"seen": {"type": "noul", "instructions": "Is the door in `frame`?"},
     "where": {"type": "choice", "instructions": "Where?", "criteria": {"left": "left third", "right": "right third"}},
     "target": {"type": "box", "instructions": "the door in `frame`"}}


def test_auto_order_is_key_then_ducky_ai_then_gateway():
    assert brains.pick("auto", True, True, True) == "typesafe"
    assert brains.pick("auto", False, True, True) == "ducky_ai"
    assert brains.pick("auto", False, False, True) == "gateway"
    assert brains.pick("auto", False, False, False) == ""
    assert brains.pick("ducky_ai", True, False, False) == "ducky_ai", "an explicit pick is kept"
    assert brains.pick("nonsense", False, True, False) == "ducky_ai"
    assert brains.pick("auto", True, True, True, ducky_selected=True) == "ducky_ai", "Ducky AI picked: Jev goes through it"
    assert brains.pick("auto", True, False, True, ducky_selected=True) == "typesafe", "Ducky AI refused: own key"
    assert brains.pick("typesafe", True, True, True, ducky_selected=True) == "typesafe", "an explicit pick wins"


def test_gateway_reply_becomes_jev_answers():
    text = 'Sure: {"seen": {"noul": 0.82}, "where": {"choice": "right", "confidence": 0.9}, "bogus": 1}'
    out = brains.parse_gateway_answers(text, Q)
    assert jev.noul(out, "seen") == 0.82
    assert jev.choice(out, "where") == ("right", 0.9)
    assert "target" not in out and "bogus" not in out
    assert brains.parse_gateway_answers('{"where": {"choice": "up"}}', Q) == {}, "unknown option dropped"
    assert brains.parse_gateway_answers('{"seen": 0.4}', Q)["seen"]["noul"] == 0.4
    assert "box" not in brains.gateway_prompt({"frame": "f"}, Q, ["f"])


def test_chat_models_read_either_way():
    assert brains.split_model("uefn_ducky", "ducky-ai") == ("uefn_ducky", "ducky-ai")
    assert brains.split_model("", "uefn_ducky:ducky-ai") == ("uefn_ducky", "ducky-ai")
    assert brains.split_model("", "") == ("", "")


def test_ducky_ai_as_the_app_model_beats_a_saved_key():
    b, calls = _brain(key="k")
    b._app_model = lambda: ("uefn_ducky", "ducky-ai")
    assert b.ready()["brain"] == "ducky_ai"
    b.decide({"frame": "f"}, Q, {"f": b"jpg"})
    assert calls[-1][0] == "ducky_ai"
    b._app_model = lambda: ("anthropic", "claude-opus-5-5")
    assert b.ready()["brain"] == "typesafe"


def _brain(key="", signed=True, ducky=None, gateway=None, settings=None):
    calls = []

    def fake_ducky(state, qs, images, model):
        calls.append(("ducky_ai", model))
        return ducky or {"ok": True, "answers": {"seen": {"type": "noul", "noul": 0.9}}, "model": model}

    def fake_gateway(state, qs, images, provider, model):
        calls.append(("gateway", provider, model, sorted(qs)))
        return gateway or {"ok": True, "answers": {}, "model": f"{provider}:{model}"}

    b = brains.Brain(jev.JevClient(key="unused"), lambda: settings or {"gateway": "openai", "gateway_model": "gpt-6-luna"},
                     ducky=fake_ducky, gateway=fake_gateway, signed=lambda: signed, key=lambda: key,
                     model=lambda: ("", ""))
    return b, calls


def test_no_key_signed_in_uses_ducky_ai_with_the_jev_model():
    b, calls = _brain()
    assert b.ready()["brain"] == "ducky_ai"
    assert b.decide({"frame": "f"}, Q, {"f": b"jpg"}, "")["ok"]
    assert calls == [("ducky_ai", jev.DEFAULT_MODEL)]
    assert b.box_ok(jev.DEFAULT_MODEL)


def test_a_refused_ducky_ai_falls_to_the_gateway_and_stays_skipped():
    b, calls = _brain(ducky={"ok": False, "error": "Ducky AI: no plan", "refused": True})
    assert b.decide({"frame": "f"}, Q, {"f": b"jpg"})["ok"]
    assert calls[-1] == ("gateway", "openai", "gpt-6-luna", ["seen", "where"]), "box question left out"
    b.decide({"frame": "f"}, Q, {"f": b"jpg"})
    assert [c[0] for c in calls] == ["ducky_ai", "gateway", "gateway"], "not asked again for a while"
    assert not b.box_ok(jev.DEFAULT_MODEL)


def test_nothing_to_answer_says_how_to_fix_it(monkeypatch):
    monkeypatch.setattr(brains, "gateway_target", lambda s: ("", ""))
    b, _ = _brain(signed=False, settings={"brain": "auto"})
    res = b.ready()
    assert not res["ok"] and res["needs_key"] and "Ducky AI" in res["error"]


def test_ducky_decide_posts_frames_to_the_jev_route():
    seen = {}

    def request(method, path, body, timeout=0):
        seen.update(method=method, path=path, body=body)
        return 200, {"payload": {"answers": {"seen": {"type": "noul", "noul": 0.7}}, "model": "jev-1.14.0"}}, ""

    out = brains.ducky_decide({"frame": "f"}, Q, {"f": b"\xff\xd8"}, "jev-1.14.0", request=request)
    assert out["ok"] and out["brain"] == "ducky_ai"
    assert seen["path"] == brains.DUCKY_AI_PATH and seen["body"]["assets"]["f"]["type"] == "image"
    refused = brains.ducky_decide({"frame": "f"}, Q, {}, "", request=lambda *a, **k: (403, {"error": "no plan"}, ""))
    assert not refused["ok"] and refused["refused"] and "no plan" in refused["error"]


def test_a_model_picker_pick_maps_to_the_brain_and_back():
    assert brains.from_pick("auto") == brains.from_pick("playtester:auto") == {"brain": "auto"}
    assert brains.from_pick("typesafe:jev-preview") == {"brain": "typesafe", "model": "jev-preview"}
    assert brains.from_pick("uefn_ducky:jev-1.14.0") == {"brain": "ducky_ai", "model": "jev-1.14.0"}
    assert brains.from_pick("openai:gpt-6-luna") == {"brain": "gateway", "gateway": "openai", "gateway_model": "gpt-6-luna"}
    assert brains.from_pick("") is None and brains.from_pick("openai:") is None
    for pick in ("playtester:auto", "typesafe:jev-preview", "uefn_ducky:jev-1.14.0", "openai:gpt-6-luna"):
        assert brains.to_pick(brains.from_pick(pick)) == pick
    assert brains.to_pick({"brain": "gateway"}) == "playtester:auto"  # no gateway model saved yet
