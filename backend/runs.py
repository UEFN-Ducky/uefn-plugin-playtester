"""Run records: one doc per run (``run.<id>``), a small summary doc per run
(``sum.<id>``) for the history list, a stop flag doc (``stop.<id>``) any process can
set, and the ``active`` lock with a heartbeat so only one run drives the game.

Frames are throwaway JPEGs in the plugin's local folder (not synced): the last
``KEEP_FRAMES`` runs keep theirs.
"""

from __future__ import annotations

import os
import random
import re
import shutil
import string
import time
from typing import Any

KEEP_FRAMES = 20
KEEP_RUNS = 50
STALE_S = 12.0
DONE = ("success", "failed", "blocked", "stopped", "timeout", "done")
_ID = re.compile(r"[^a-z0-9-]+")
_SUMMARY = ("id", "goal", "target", "success", "interact", "dry_run", "model", "status", "reason",
            "started", "ended", "n_steps", "seconds", "avg_ms", "jev_ms", "last_frame", "origin",
            "target_actor", "target_point", "beacon", "clip_id", "clip_file", "clip_error")


def clean_id(run_id: Any) -> str:
    return _ID.sub("", str(run_id or "").strip().lower())[:64]


def new_id(now: float | None = None) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now or time.time()))
    tail = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(4))
    return f"r-{stamp}-{tail}"


def summary(run: dict[str, Any]) -> dict[str, Any]:
    return {k: run.get(k) for k in _SUMMARY}


class Runs:
    def __init__(self, data: Any, frames_dir: str) -> None:
        self.data = data
        self.frames_dir = frames_dir

    # ---- docs

    def get(self, run_id: str) -> dict[str, Any] | None:
        rid = clean_id(run_id)
        doc = self.data.get(f"run.{rid}") if rid else None
        return doc if isinstance(doc, dict) else None

    def save(self, run: dict[str, Any]) -> None:
        self.data.put(f"run.{run['id']}", run)
        self.data.put(f"sum.{run['id']}", summary(run))

    def summaries(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = [r for r in (self.data.items("sum.") or {}).values() if isinstance(r, dict)]
        rows.sort(key=lambda r: float(r.get("started") or 0), reverse=True)
        return rows[: max(1, int(limit))]

    def latest(self) -> dict[str, Any] | None:
        rows = self.summaries(1)
        return rows[0] if rows else None

    # ---- the one active run

    def active(self, now: float | None = None) -> dict[str, Any] | None:
        doc = self.data.get("active")
        if not isinstance(doc, dict) or not doc.get("run_id"):
            return None
        if (now or time.time()) - float(doc.get("heartbeat") or 0) > STALE_S:
            return None  # its process is gone; the lock is free
        return doc

    def claim(self, run_id: str, now: float | None = None) -> dict[str, Any] | None:
        """Take the lock, or return the run that holds it."""
        held = self.active(now)
        if held and held.get("run_id") != run_id:
            return held
        self.beat(run_id, now)
        return None

    def beat(self, run_id: str, now: float | None = None) -> None:
        self.data.put("active", {"run_id": run_id, "pid": os.getpid(), "heartbeat": now or time.time()})

    def release(self, run_id: str) -> None:
        doc = self.data.get("active")
        if isinstance(doc, dict) and doc.get("run_id") == run_id:
            self.data.delete("active")

    # ---- stop flag (works across the app and the agents' bridge process)

    def request_stop(self, run_id: str) -> None:
        self.data.put(f"stop.{clean_id(run_id)}", {"at": time.time()})

    def stop_requested(self, run_id: str) -> bool:
        return self.data.get(f"stop.{clean_id(run_id)}") is not None

    def clear_stop(self, run_id: str) -> None:
        self.data.delete(f"stop.{clean_id(run_id)}")

    # ---- frames

    def frame_path(self, run_id: str, n: int) -> str:
        return os.path.join(self.frames_dir, clean_id(run_id), f"{int(n):03d}.jpg")

    def save_frame(self, run_id: str, n: int, jpeg: bytes) -> str:
        path = self.frame_path(run_id, n)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(jpeg)
        return path

    def read_frame(self, run_id: str, n: int) -> bytes | None:
        try:
            with open(self.frame_path(run_id, n), "rb") as fh:
                return fh.read()
        except OSError:
            return None

    def prune(self) -> None:
        """Keep the newest runs' frames and docs; never touches a running run."""
        rows = self.summaries(10_000)
        for i, row in enumerate(rows):
            rid = clean_id(row.get("id"))
            if not rid or row.get("status") not in DONE:
                continue
            if i >= KEEP_FRAMES:
                shutil.rmtree(os.path.join(self.frames_dir, rid), ignore_errors=True)
            if i >= KEEP_RUNS:
                self.data.delete(f"run.{rid}")
                self.data.delete(f"sum.{rid}")
                self.data.delete(f"stop.{rid}")
