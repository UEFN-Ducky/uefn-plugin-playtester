"""On a Mac (no Fortnite client) Play Tester says so instead of touching Win32."""

from __future__ import annotations

import sys

from uefn_plugin_playtester import capture, screen, windows


def test_mac_has_no_client_and_points_at_the_paired_pc(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    out = screen.client()
    assert out["ok"] is False and "paired Windows PC" in out["error"]
    assert windows.list_windows() == []
    assert capture.window_image(1234) is None
