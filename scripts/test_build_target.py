"""--target in build_zip.py: zip names per platform, host-only builds, python_abi check."""

import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_zip as bz  # noqa: E402


@pytest.mark.parametrize("machine,want", [("arm64", "macos-arm64"), ("x86_64", "macos-x64")])
def test_mac_host_target(monkeypatch, machine, want):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr("platform.machine", lambda: machine)
    assert bz.host_target() == want
    assert bz.compiled_dest("p", 3, want).name == f"p-3-{want}-compiled.ducky-plugin.zip"
    assert f"cpython-313-darwin-{machine}".endswith(bz.TARGETS[want])


def test_names_per_target(monkeypatch):
    monkeypatch.setattr(bz, "host_target", lambda: "linux-x86_64")
    assert bz.compiled_dest("p", 3, "linux-x86_64").name == "p-3-linux-x86_64-compiled.ducky-plugin.zip"
    monkeypatch.setattr(bz, "host_target", lambda: "windows")
    assert bz.compiled_dest("p", 3, "windows").name == "p-3-compiled.ducky-plugin.zip"  # unchanged Windows name


def test_refuses_cross_and_unknown_targets(monkeypatch):
    monkeypatch.setattr(bz, "host_target", lambda: "linux-x86_64")
    with pytest.raises(SystemExit, match="arm64 machine"):
        bz.compiled_dest("p", 1, "linux-arm64")
    with pytest.raises(SystemExit, match="unknown"):
        bz.compiled_dest("p", 1, "mac")


def test_host_target_is_a_known_target():
    assert bz.host_target() in bz.TARGETS


def test_zip_abi_must_match_target(tmp_path):
    z = tmp_path / "b.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("plugin.json", json.dumps({"python_abi": "cpython-313-x86_64-linux-gnu"}))
    bz.check_zip_target(z, "linux-x86_64")
    with pytest.raises(SystemExit, match="not linux-arm64"):
        bz.check_zip_target(z, "linux-arm64")
