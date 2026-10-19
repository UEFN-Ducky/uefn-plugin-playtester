"""Load the plugin's backend package the way the host does (its own package name)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

NAME = "uefn_plugin_playtester"
ROOT = Path(__file__).resolve().parent.parent / "backend"

if NAME not in sys.modules:
    spec = importlib.util.spec_from_file_location(NAME, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[NAME] = mod
    spec.loader.exec_module(mod)
