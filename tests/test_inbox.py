"""Commands into the session: the inbox write, the GetVerseOutput read, finding the inbox, timeouts and refusals."""

from __future__ import annotations

import json
import re

from uefn_plugin_playtester import inbox

COMP = "/P/Map.Map:PersistentLevel.LevelEntity.LevelEntity.PlayTesterInbox_1.Verse-PlayTester-playtester_inbox_component_0"


class FakeEpic:
    """Answers the way UEFN 43.00 did live: the inbox component prints [PTREPLY] into the Verse output."""

    def __init__(self, answer=True):
        self.answer = answer
        self.writes = []
        self.output = [{"message": "[PTREPLY] 0 ok ready"}, {"message": "[SS] registered, 37 cabinets"}]

    def call_tool(self, name, args):
        assert name == "unreal__call_tool"
        tool, a = args["tool_name"], args["arguments"]
        if tool == "SetComponentProperty":
            assert args["toolset_name"] == inbox.ENTITY_TOOLSET and a["propertyName"] == "Inbox"
            text = json.loads(a["value"])  # Epic wants the value JSON-encoded
            self.writes.append(text)
            if self.answer:
                for line in text.split(";"):
                    seq, tool_name = line.split(" ")[:2]
                    said = "ok pong" if tool_name == "ping" else f"fail unknown tool {tool_name}"
                    self.output.append({"message": f"[PTREPLY] {seq} {said}"})
            return {"returnValue": None}
        if tool == "GetVerseOutput":
            assert args["toolset_name"] == inbox.SESSION_TOOLSET
            rx = re.compile(a["pattern"], re.I)
            return {"returnValue": {"entries": [e for e in self.output if rx.search(e["message"])]}}
        if tool == "FindEntities":
            assert a["nameFilter"] == "PlayTesterInbox"
            return json.dumps({"returnValue": [{"entity": {"refPath": COMP.rsplit(".", 1)[0]}}]})  # text answers too
        if tool == "GetComponents":
            return {"returnValue": [{"component": {"refPath": a["entity"]["refPath"] + ".transform_component_0"}},
                                    {"component": {"refPath": COMP}}]}
        raise AssertionError(tool)


def clock():
    return 100.0


def test_ping_and_unknown_tool_get_their_own_replies():
    e = FakeEpic()
    out = inbox.send(e.call_tool, "ping; fly", COMP, clock=clock, sleep=lambda _: None)
    assert [r["text"] for r in out["replies"]] == ["pong", "unknown tool fly"]
    assert out["ok"] is False  # one failed
    seqs = [int(line.split(" ")[0]) for line in e.writes[0].split(";")]
    assert seqs[1] == seqs[0] + 1


def test_old_replies_and_other_prints_are_ignored():
    e = FakeEpic()
    e.output.append({"message": "[PTREPLY] 1 ok pong"})  # an earlier send's answer
    out = inbox.send(e.call_tool, "ping", COMP, clock=clock, sleep=lambda _: None)
    assert out["ok"] is True and len(out["replies"]) == 1 and out["replies"][0]["seq"] > 1


def test_seqs_keep_rising_across_calls():
    e = FakeEpic()
    inbox.send(e.call_tool, "ping", COMP, clock=clock, sleep=lambda _: None)
    inbox.send(e.call_tool, "ping", COMP, clock=clock, sleep=lambda _: None)
    first, second = (int(w.split(" ")[0]) for w in e.writes)
    assert second > first


def test_no_session_times_out_with_a_reason():
    e = FakeEpic(answer=False)
    t = {"now": 100.0}

    def ticking():
        t["now"] += 1.0
        return t["now"]

    out = inbox.send(e.call_tool, "ping", COMP, timeout_s=2, clock=ticking, sleep=lambda _: None)
    assert out["ok"] is False and "no reply" in out["replies"][0]["text"]


def test_empty_commands_and_missing_component_never_write():
    e = FakeEpic()
    assert inbox.send(e.call_tool, " ; ", COMP)["ok"] is False
    assert inbox.send(e.call_tool, "ping", "")["ok"] is False
    assert e.writes == []


def test_epic_refusal_is_reported():
    out = inbox.send(lambda *_: {"ok": False, "error": "no such component"}, "ping", COMP)
    assert out["ok"] is False and "no such component" in out["error"]


def test_finds_the_inbox_component_on_the_named_entity():
    assert inbox.find_component(FakeEpic().call_tool) == COMP
    assert inbox.find_component(lambda *_: {"returnValue": []}) == ""


def test_reply_parser_reads_a_live_line():
    line = "[PTREPLY] 1794370000002 ok 0,-128163.335447,50338.926123,565.382947,100.000000"
    assert inbox.parse_reply(line) == {"seq": 1794370000002, "ok": True,
                                       "text": "0,-128163.335447,50338.926123,565.382947,100.000000"}
