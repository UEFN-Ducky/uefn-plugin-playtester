"""Record a play test through Island Media Kit's capture tools (imk_record_start / imk_record_stop).

Without Media Kit (or on a host without api.call_tool) recording is skipped quietly: the run still plays.
"""

from __future__ import annotations

from typing import Any


class MediaKitRecorder:
    def __init__(self, api: Any) -> None:
        self.api = api

    def start(self, run_id: str, target: str = "fortnite") -> str | None:
        """The capture id, or None when Media Kit can't record now (``target``: its capture target)."""
        call = getattr(self.api, "call_tool", None)
        if not callable(call):
            return None
        try:
            out = call("imk_record_start", {"target": target or "fortnite", "label": f"Play Tester {run_id}"[:80]})
        except Exception:  # Media Kit not installed or the tool refused
            return None
        rec = out.get("record") if isinstance(out, dict) and out.get("ok") else None
        return str(rec["id"]) if isinstance(rec, dict) and rec.get("id") else None

    def stop(self, record_id: str) -> dict[str, Any]:
        """Stop the capture: ``{clip_id, clip_file}`` or ``{clip_error}``."""
        try:
            out = self.api.call_tool("imk_record_stop", {"record_id": record_id})
        except Exception as exc:
            return {"clip_error": str(exc)[:200]}
        if not isinstance(out, dict) or not out.get("ok"):
            return {"clip_error": str((out or {}).get("error") if isinstance(out, dict) else out)[:200]}
        media = out.get("media") if isinstance(out.get("media"), dict) else {}
        item = media.get("item") if isinstance(media.get("item"), dict) else {}
        return {"clip_id": str(item.get("id") or record_id), "clip_file": out.get("file") or ""}
