"""Work that only Ducky's window process can do (its Browser tab pages), from any process.

Agents' tools run in a tool server, a separate process from Ducky's window; the Browser tab's
page views live in the window. ``run`` does the work here when this is the window, else sends it
to this plugin's panel call of the same name over Ducky's local panel API (the Browser plugin
does the same).
"""

from __future__ import annotations

import json
from typing import Any, Callable

PLUGIN_ID = "playtester"


def in_app() -> bool:
    try:
        from frontend.ui_web import browser_overlay
    except Exception:
        return False
    return getattr(browser_overlay, "_main_window", None) is not None


def call_app(method: str, params: dict[str, Any], timeout_s: float = 30.0) -> dict[str, Any]:
    """This plugin's panel call ``method`` in Ducky's window process."""
    import urllib.request

    try:
        from frontend.settings import PANEL_LISTENER_PORT
    except Exception:
        return {"ok": False, "error": "This UEFN Ducky can't reach its window from a tool: update the app."}
    body = json.dumps({"args": {"plugin_id": PLUGIN_ID, "method": method, "params": params}}).encode("utf-8")
    request = urllib.request.Request(f"http://127.0.0.1:{PANEL_LISTENER_PORT - 1}/__panel_api/plugin_call",
                                     data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": f"Ducky's window didn't answer ({exc}). Is Ducky open?"}
    result = payload.get("result", payload) if isinstance(payload, dict) else None
    return result if isinstance(result, dict) else {"ok": False, "error": "Ducky's window sent an unreadable answer."}


def run(method: str, local: Callable[..., dict[str, Any]], params: dict[str, Any], timeout_s: float = 30.0) -> dict[str, Any]:
    return local(**params) if in_app() else call_app(method, params, timeout_s)
