"""Commands into a running Fortnite session: the beacon file's ``playtester_inbox_component``.

Ducky writes the component's ``Inbox`` string through Epic MCP (live edit carries it into the
session in about 0.1 s); Verse runs each command once and prints ``[PTREPLY] <seq> ok|fail <text>``,
read back with Epic GetVerseOutput (verified live on 43.00, about 0.1 s). Commands: ping, players, teleport, health, mark.
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, Callable

ENTITY_TOOLSET = "ValkyrieToolset.EntityToolset"
SESSION_TOOLSET = "ValkyrieFortniteToolset.SessionToolset"
PROPERTY = "Inbox"
ENTITY_NAME = "PlayTesterInbox"
_REPLY = re.compile(r"\[PTREPLY\] (\d+) (ok|fail) ?(.*)$")
_seq_lock = threading.Lock()
_last_seq = 0


def _next_seqs(n: int) -> list[int]:
    """Always increasing (ms clock), so a rebuilt sender never reuses a seq the inbox already ran."""
    global _last_seq
    with _seq_lock:
        start = max(_last_seq + 1, int(time.time() * 1000))
        _last_seq = start + n - 1
    return list(range(start, start + n))


def parse_commands(commands: Any) -> list[str]:
    raw = commands if isinstance(commands, list) else str(commands or "").replace("\n", ";").split(";")
    return [" ".join(str(c).split()) for c in raw if str(c).strip()]


def parse_reply(text: str) -> dict[str, Any] | None:
    m = _REPLY.search(text or "")
    return {"seq": int(m.group(1)), "ok": m.group(2) == "ok", "text": m.group(3).strip()} if m else None


def _result(out: Any) -> Any:
    """Epic answers {"returnValue": ...}, sometimes as JSON text."""
    if isinstance(out, str):
        try:
            out = json.loads(out)
        except ValueError:
            return {}
    return out.get("returnValue", out) if isinstance(out, dict) else {}


def verse_replies(call_tool: Callable[[str, dict[str, Any]], Any], seqs: list[int]) -> list[str]:
    """This send's [PTREPLY] lines from the session's Verse output (Epic GetVerseOutput, server side)."""
    pattern = r"\[PTREPLY\] (" + "|".join(str(s) for s in seqs) + r") "
    out = _result(call_tool("unreal__call_tool", {"toolset_name": SESSION_TOOLSET, "tool_name": "GetVerseOutput",
                                                  "arguments": {"pattern": pattern, "maxResults": 200}}))
    rows = out.get("entries") if isinstance(out, dict) else None
    return [str(r.get("message") or "") for r in rows or [] if isinstance(r, dict)]


def find_component(call_tool: Callable[[str, dict[str, Any]], Any]) -> str:
    """The playtester_inbox_component on an entity named PlayTesterInbox ('' when none is placed)."""
    # ponytail: looks only at entities named ENTITY_NAME; a component on another entity needs component=.
    found = _result(call_tool("unreal__call_tool", {"toolset_name": ENTITY_TOOLSET, "tool_name": "FindEntities",
                                                    "arguments": {"nameFilter": ENTITY_NAME}}))
    for row in found if isinstance(found, list) else []:
        entity = (row.get("entity") or {}).get("refPath") if isinstance(row, dict) else ""
        if not entity:
            continue
        comps = _result(call_tool("unreal__call_tool", {"toolset_name": ENTITY_TOOLSET, "tool_name": "GetComponents",
                                                        "arguments": {"entity": {"refPath": entity}}}))
        for comp in comps if isinstance(comps, list) else []:
            path = (comp.get("component") or {}).get("refPath") or "" if isinstance(comp, dict) else ""
            if "playtester_inbox_component" in path:
                return path
    return ""


def send(call_tool: Callable[[str, dict[str, Any]], Any], commands: Any, component: str, timeout_s: float = 5.0,
         clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    cmds = parse_commands(commands)
    if not cmds:
        return {"ok": False, "error": "Say what to send: ping, players, teleport <player> <x> <y> <z> [yaw], "
                                      "health <player> [value] or mark <text> (several with ';')."}
    if not component:
        return {"ok": False, "error": "Say which inbox component to write (its path from EntityToolset FindEntities)."}
    seqs = _next_seqs(len(cmds))
    text = ";".join(f"{s} {c}" for s, c in zip(seqs, cmds))
    started = clock()
    try:
        out = call_tool("unreal__call_tool", {"toolset_name": ENTITY_TOOLSET, "tool_name": "SetComponentProperty",
                                              "arguments": {"component": {"refPath": component},
                                                            "propertyName": PROPERTY, "value": json.dumps(text)}})
    except Exception as exc:
        return {"ok": False, "error": f"Could not write the inbox through Epic MCP: {exc}"}
    if isinstance(out, dict) and out.get("ok") is False:
        return {"ok": False, "error": f"Epic MCP refused the inbox write: {out.get('error') or out}"}
    want = set(seqs)
    got: dict[int, dict[str, Any]] = {}
    deadline = started + max(0.5, min(float(timeout_s or 5.0), 30.0))
    while True:
        try:
            lines = verse_replies(call_tool, seqs)
        except Exception:
            lines = []  # ponytail: a missed read is retried until the deadline
        for line in lines:
            reply = parse_reply(line)
            if reply and reply["seq"] in want:
                got[reply["seq"]] = reply
        if len(got) == len(want) or clock() >= deadline:
            break
        sleep(0.1)
    replies = [got.get(s) or {"seq": s, "ok": False, "text": "no reply (is a session running with the inbox entity?)"}
               for s in seqs]
    for cmd, reply in zip(cmds, replies):
        reply["command"] = cmd
    return {"ok": all(r["ok"] for r in replies), "replies": replies, "seconds": round(clock() - started, 3)}
