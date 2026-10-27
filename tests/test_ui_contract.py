"""The tab's script and the backend agree: per-tester calls carry the tester, every call exists."""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import uefn_plugin_playtester as plugin

from test_testers import FakeApi

JS = (Path(__file__).resolve().parent.parent / "ui" / "playtester.js").read_text(encoding="utf-8")
HTML = (Path(__file__).resolve().parent.parent / "ui" / "index.html").read_text(encoding="utf-8")
NOT_PER_TESTER = {"testers", "targets", "open", "close", "rename"}


def calls(kind: str) -> set[str]:
    return set(re.findall(rf"(?<![\w.]){kind}\(\"([a-z_]+)\"", JS))


def test_per_tester_calls_go_through_trpc(tmp_path):
    api = FakeApi(tmp_path)
    plugin.register(api)
    plain, scoped = calls("rpc"), calls("trpc")
    assert scoped and plain <= NOT_PER_TESTER, plain - NOT_PER_TESTER
    for name in plain | scoped:
        assert name in api.rpcs, name
    for name in scoped:
        assert "tester" in inspect.signature(api.rpcs[name]).parameters, name


def test_every_help_popover_the_script_opens_exists():
    for key in set(re.findall(r'\["(game|jev|positions|tabs)"', JS)):
        assert f'id="help-{key}"' in HTML, key
    for el in ("tabs", "addTester", "newMenu", "newPresets", "newWindows", "newWeb", "newUrl", "newProfile"):
        assert f'id="{el}"' in HTML, el
