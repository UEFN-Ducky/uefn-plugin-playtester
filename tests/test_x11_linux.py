"""Linux: find a real X window, bring it forward, type into it with XTest and capture it (run under xvfb-run)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import types

import pytest

from uefn_plugin_playtester import capture, focus, inputs, screen, windows, x11

TITLE = "PT Linux Test"


def test_no_display_names_what_is_missing(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(x11, "_display", None)
    with pytest.raises(RuntimeError, match="DISPLAY is not set"):
        x11.display()


linux_x = pytest.mark.skipif(not sys.platform.startswith("linux") or not os.environ.get("DISPLAY") or not shutil.which("xterm"),
                             reason="needs Linux, an X display (xvfb-run) and xterm")


@linux_x
def test_fortnite_window_is_found_and_waits_for_play_mode(monkeypatch):
    fake = types.ModuleType("backend.tools.uefn.fortnite_client")
    fake.gameplay_loaded_for = lambda path: None  # no play mode in the log yet
    monkeypatch.setitem(sys.modules, "backend.tools.uefn.fortnite_client", fake)
    assert screen.client()["error"] == screen.NO_CLIENT
    proc = subprocess.Popen(["xterm", "-T", "Fortnite", "-geometry", "100x30+0+0", "-e", "sleep", "30"])
    try:
        end = time.monotonic() + 10
        while not screen.client()["ok"] and time.monotonic() < end:
            time.sleep(0.1)
        st = screen.client()
        assert st["ok"] and st["hwnd"] and st["gameplay_loaded"] is False
        assert screen.ready(wait=False)["loading"] is True  # nothing is pressed while it loads
    finally:
        proc.kill()


@linux_x
def test_find_focus_type_and_capture(tmp_path):
    typed = tmp_path / "typed.txt"
    proc = subprocess.Popen(["xterm", "-T", TITLE, "-geometry", "100x30+0+0", "-e", "sh", "-c",
                             f'read line; printf %s "$line" > "{typed}"'])
    try:
        rows: list = []
        end = time.monotonic() + 10
        while not rows and time.monotonic() < end:
            rows = windows.find(title=TITLE)
            time.sleep(0.1)
        assert rows, "the xterm window never showed up"
        row = rows[0]
        hwnd = int(row["hwnd"])
        if row["pid"]:
            assert row["exe"] == "xterm" and row["pid"] == proc.pid

        front = focus.bring_to_front(hwnd, allow_click=False)
        assert front["ok"], front
        assert screen.foreground() == hwnd

        ctrl = inputs.Controller(hwnd)
        assert ctrl.hold("A", 0.05)["released"]
        assert ctrl.hold("ENTER", 0.05)["released"]
        end = time.monotonic() + 5
        while not typed.exists() and time.monotonic() < end:
            time.sleep(0.05)
        assert typed.read_text() == "a"

        proc.wait(5)  # read finished: start a window that stays for the pictures
        proc = subprocess.Popen(["xterm", "-T", TITLE, "-geometry", "100x30+0+0", "-e", "sleep", "30"])
        rows = []
        end = time.monotonic() + 10
        while not rows and time.monotonic() < end:
            rows = windows.find(title=TITLE)
            time.sleep(0.1)
        hwnd, w, h = int(rows[0]["hwnd"]), rows[0]["width"], rows[0]["height"]
        time.sleep(0.5)  # first draw
        img = capture.window_image(hwnd)
        assert img is not None and img.size == (w, h)
        frame = screen.grab(hwnd, 512)
        assert max(frame["width"], frame["height"]) == 512 and frame["jpeg"][:2] == b"\xff\xd8"
    finally:
        proc.kill()
