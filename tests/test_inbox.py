"""Commands into the session: the inbox write, the [PTREPLY] wait, timeouts and refusals."""

from __future__ import annotations

import json

from uefn_plugin_playtester import beacon, inbox


class FakeSession:
    """Answers each write the way playtester_inbox_component does, into a fake log."""

    def __init__(self, answer=True):
        self.answer = answer
        self.writes = []
        self.prints = []

    def call_tool(self, name, args):
        assert name == "unreal__call_tool"
        assert args["toolset_name"] == inbox.ENTITY_TOOLSET and args["tool_name"] == "SetComponentProperty"
        text = json.loads(args["arguments"]["value"])  # Epic wants the value JSON-encoded
        self.writes.append(text)
        if self.answer:
            for line in text.split(";"):
                seq, tool = line.split(" ")[:2]
                said = "ok pong" if tool == "ping" else f"fail unknown tool {tool}"
                self.prints.append({"t": 100.0, "text": f"[PTREPLY] {seq} {said}"})
        return {"ok": True}

    def prints_since(self, since):
        return [p for p in self.prints if p["t"] >= since - 0.5]


def clock():
    return 100.0


def test_ping_and_unknown_tool_get_their_own_replies():
    s = FakeSession()
    out = inbox.send(s.call_tool, s.prints_since, "ping; fly", "/E:playtester_inbox_component_0", clock=clock, sleep=lambda _: None)
    assert [r["text"] for r in out["replies"]] == ["pong", "unknown tool fly"]
    assert out["ok"] is False  # one failed
    seqs = [int(line.split(" ")[0]) for line in s.writes[0].split(";")]
    assert seqs[1] == seqs[0] + 1


def test_seqs_keep_rising_across_calls():
    s = FakeSession()
    inbox.send(s.call_tool, s.prints_since, "ping", "c", clock=clock, sleep=lambda _: None)
    inbox.send(s.call_tool, s.prints_since, "ping", "c", clock=clock, sleep=lambda _: None)
    first, second = (int(w.split(" ")[0]) for w in s.writes)
    assert second > first


def test_no_session_times_out_with_a_reason():
    s = FakeSession(answer=False)
    t = {"now": 100.0}

    def ticking():
        t["now"] += 1.0
        return t["now"]

    out = inbox.send(s.call_tool, s.prints_since, "ping", "c", timeout_s=2, clock=ticking, sleep=lambda _: None)
    assert out["ok"] is False and "no reply" in out["replies"][0]["text"]


def test_empty_commands_and_missing_component_never_write():
    s = FakeSession()
    assert inbox.send(s.call_tool, s.prints_since, " ; ", "c")["ok"] is False
    assert inbox.send(s.call_tool, s.prints_since, "ping", "")["ok"] is False
    assert s.writes == []


def test_epic_refusal_is_reported():
    out = inbox.send(lambda *_: {"ok": False, "error": "no such component"}, lambda _: [], "ping", "c")
    assert out["ok"] is False and "no such component" in out["error"]


def test_beacon_log_line_reaches_the_reply_parser():
    line = "[2026.11.10-12.00.00:123][  5]LogVerse: : [PTREPLY] 1762776000123 ok 0,10.5,-3.0,120.0,100.0"
    verse = beacon.parse_verse(line)
    assert inbox.parse_reply(verse["text"]) == {"seq": 1762776000123, "ok": True, "text": "0,10.5,-3.0,120.0,100.0"}
