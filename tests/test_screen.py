"""No Fortnite window must never read as "open, loading", whichever way the app says it."""

from __future__ import annotations

import sys
import types

import pytest

from uefn_plugin_playtester import screen


@pytest.mark.parametrize("answer", [
    {"ok": True, "running": False, "hint": "No Fortnite client is open."},
    {"ok": False, "error": "Expected exactly one visible Fortnite client window; found 0"},
])
def test_no_client_window_is_not_open(monkeypatch, answer):
    fake = types.ModuleType("backend.tools.uefn.fortnite_client")
    fake.client_status = lambda: answer
    monkeypatch.setitem(sys.modules, "backend.tools.uefn.fortnite_client", fake)
    monkeypatch.setattr(sys, "platform", "win32")
    out = screen.client()
    assert out["ok"] is False
    assert "No Fortnite client is open" in out["error"]
