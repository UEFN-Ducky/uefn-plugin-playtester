"""The Fortnite client of a UEFN session: Ducky's client finder and play-mode gate, the Verse
beacon's world positions, and the open island's actors (from the UEFN editor) as targets."""

from __future__ import annotations

import time
from typing import Any, Callable

from .. import beacon, geo, learning, screen
from .window import WindowTarget


class FortniteTarget(WindowTarget):
    kind = "fortnite"
    recorder_target = "fortnite"
    default_profile = "fortnite"

    def __init__(self, spec: dict[str, Any] | None = None, *, api: Any = None, data: Any = None,
                 island: Callable[[], str] | None = None, beacon_: Any = None) -> None:
        super().__init__(spec or {})
        self.api = api
        self.data = data
        self._island = island or (lambda: "")
        self.beacon = beacon_ if beacon_ is not None else beacon.Beacon()

    def default_label(self) -> str:
        return "Fortnite"

    def info(self) -> dict[str, Any]:
        return {**super().info(), "island": self.place()}

    def place(self) -> str:
        try:
            return str(self._island() or "")
        except Exception:
            return ""

    # The client is Ducky's own find: one Fortnite window, play mode, settled.
    def status(self) -> dict[str, Any]:
        st = screen.client()
        if st.get("ok"):
            return st
        said = str(st.get("error") or "")
        if said.startswith("No Fortnite client"):
            return {**st, "title": "Fortnite isn't open", "fix": "Press Launch Session in UEFN and wait for the island."}
        if said.startswith("More than one"):
            return {**st, "title": "Two Fortnite windows are open", "fix": "Close the extra one."}
        return st

    def ready(self, wait: bool = True) -> dict[str, Any]:
        return screen.ready(wait=wait)

    # ------------------------------------------------------------------ the island's layout

    def map_key(self) -> str:
        return f"map.{learning.key(self.place(), 40) or 'island'}"

    def level_map(self, refresh: bool = False) -> dict[str, Any]:
        """Every device's label and position in the open island, read once from the UEFN editor and kept."""
        doc = self.data.get(self.map_key()) if self.data is not None else None
        if isinstance(doc, dict) and doc.get("actors") and not refresh:
            return {"ok": True, **doc, "cached": True}
        try:
            out = self.api.listener("actor_state_snapshot", {"limit": 600}, timeout=30)
        except Exception:
            if isinstance(doc, dict) and doc.get("actors"):
                return {"ok": True, **doc, "cached": True, "note": "UEFN isn't connected: this is the last map read."}
            return {"ok": False, "error": "UEFN isn't connected, so Play Tester can't read the level: open the island in UEFN."}
        if isinstance(out, dict) and "actors" not in out and isinstance(out.get("result"), dict):
            out = out["result"]
        actors = []
        for r in (out or {}).get("actors") or []:
            pt = geo.point((r or {}).get("location"))
            if pt and r.get("label"):
                actors.append({"label": str(r["label"]), "class": str(r.get("class") or ""), **{k: round(v, 1) for k, v in pt.items()}})
        doc = {"island": self.place(), "at": time.time(), "count": len(actors), "actors": actors}
        try:
            self.data.put(self.map_key(), doc)
        except Exception:
            pass
        return {"ok": True, **doc, "cached": False}

    def landmarks(self) -> list[dict[str, Any]]:
        doc = self.data.get(self.map_key()) if self.data is not None else None
        rows = (doc or {}).get("actors") if isinstance(doc, dict) else None
        return [{"name": r["label"], "x": r["x"], "y": r["y"], "z": r["z"], "source": "level"} for r in rows or []]

    def _from_map(self, label: str) -> dict[str, Any] | None:
        for r in self.landmarks():
            if r["name"].lower() == label.lower():
                return {"ok": True, "point": {k: float(r[k]) for k in ("x", "y", "z")}, "label": r["name"], "from": "saved map"}
        return None

    def locate(self, label: str) -> dict[str, Any]:
        """A level actor's position by its label, from the UEFN editor (or the saved map when it is busy)."""
        try:
            out = self.api.listener("actor_state_snapshot", {"label_filter": label, "limit": 20, "scope": "all"}, timeout=20)
        except Exception:
            saved = self._from_map(label)
            if saved:
                return saved
            return {"ok": False, "error": "UEFN isn't connected, so Play Tester can't look the actor up: open the project in UEFN or give target_xyz."}
        if isinstance(out, dict) and "actors" not in out and isinstance(out.get("result"), dict):
            out = out["result"]
        rows = [r for r in ((out or {}).get("actors") or []) if isinstance(r, dict)]
        exact = [r for r in rows if str(r.get("label") or "").lower() == label.lower()]
        row = exact[0] if exact else (rows[0] if len(rows) == 1 else None)
        if row is None:
            near = ", ".join(str(r.get("label")) for r in rows[:8])
            return {"ok": False, "error": f"No actor labelled {label!r} in the level" + (f"; close: {near}" if near else ".")}
        pt = geo.point(row.get("location"))
        if not pt:
            return {"ok": False, "error": f"{row.get('label')} has no readable location."}
        return {"ok": True, "point": pt, "label": str(row.get("label"))}
