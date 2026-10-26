"""Many testers, each its own game: open, find again, close.

The list lives in the plugin's data under ``testers`` so Ducky's window and agents' tool servers
(separate processes) see the same testers; each process builds its own Tester objects from it
(a run plays in the process that started it; stops and results go through the data, as before).
Each new tester keeps its runs under its own key prefix (``t.<id>.``) and frames folder; the
default tester (the Fortnite client) keeps the plugin's original keys.
"""

from __future__ import annotations

import re
import shutil
import threading
import time
from typing import Any, Callable

from . import profiles, runs, targets
from .tester import DEFAULT, Tester

DOC = "testers"
MAX_TESTERS = 12


class ScopedData:
    """``api.data`` seen through a key prefix: one tester's runs, lessons and maps."""

    def __init__(self, data: Any, prefix: str) -> None:
        self.data, self.prefix = data, prefix

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(self.prefix + key, default)

    def put(self, key: str, value: Any) -> Any:
        return self.data.put(self.prefix + key, value)

    def delete(self, key: str) -> Any:
        return self.data.delete(self.prefix + key)

    def items(self, prefix: str) -> dict[str, Any]:
        n = len(self.prefix)
        return {k[n:]: v for k, v in (self.data.items(self.prefix + prefix) or {}).items()}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")[:24] or "game"


class TesterManager:
    def __init__(self, api: Any, *, settings: Callable[[], dict[str, Any]], save_setting: Callable[[str, Any], None],
                 push: Callable[[dict[str, Any]], None] | None = None, island: Callable[[], str] | None = None,
                 recorder: Any = None, lease: Any = None, client: Any = None) -> None:
        self.api = api
        self._settings, self._save_setting = settings, save_setting
        self._push, self._island, self._recorder = push, island, recorder
        self.lease = lease
        self._client = client          # a shared Brain for the default tester (keeps 1.1.x behaviour and tests)
        self._made: dict[str, Tester] = {}
        self._chat_pick: dict[str, str] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ the saved list

    def _doc(self) -> dict[str, Any]:
        doc = self.api.data.get(DOC)
        doc = doc if isinstance(doc, dict) else {}
        items = doc.get("items") if isinstance(doc.get("items"), dict) else {}
        order = [i for i in (doc.get("order") or []) if i in items]
        items[DEFAULT] = {"id": DEFAULT, "label": "Fortnite", "spec": {"kind": "fortnite"}, **(items.get(DEFAULT) or {})}
        items[DEFAULT]["spec"] = {"kind": "fortnite", **(items[DEFAULT].get("spec") or {})}
        order = [DEFAULT] + [i for i in order if i != DEFAULT] + [i for i in items if i not in order and i != DEFAULT]
        return {"order": order, "items": items}

    def _save(self, doc: dict[str, Any]) -> None:
        self.api.data.put(DOC, {"order": doc["order"], "items": doc["items"]})

    def _save_one(self, item: dict[str, Any]) -> None:
        with self._lock:
            doc = self._doc()
            if item["id"] in doc["items"]:
                doc["items"][item["id"]] = item
                self._save(doc)

    # ------------------------------------------------------------------ building testers

    def _build(self, item: dict[str, Any]) -> Tester:
        tid = item["id"]
        data = self.api.data if tid == DEFAULT else ScopedData(self.api.data, f"t.{tid}.")
        frames = self.api.local_dir("frames" if tid == DEFAULT else f"frames-{tid}")
        target = targets.make(dict(item.get("spec") or {}), api=self.api, data=data, island=self._island)
        return Tester(tid, item, store=runs.Runs(data, frames), target=target, settings=self._settings,
                      save_setting=self._save_setting, push=self._push, lease=self.lease, recorder=self._recorder,
                      client=self._client if tid == DEFAULT else None, save_doc=self._save_one)

    def _sync(self) -> dict[str, Any]:
        """Build testers another process opened, drop ones it closed; returns the saved list."""
        doc = self._doc()
        with self._lock:
            for tid in list(self._made):
                if tid not in doc["items"]:
                    gone = self._made.pop(tid)
                    gone.pilot.stop("")
            for tid in doc["order"]:
                item = doc["items"][tid]
                have = self._made.get(tid)
                if have is None or have.doc.get("spec") != item.get("spec"):
                    if have is not None and have.store.active():
                        continue  # never rebuild a tester under its running run
                    self._made[tid] = self._build(item)
                else:
                    have.doc = dict(item)
        return doc

    def list(self) -> list[Tester]:
        doc = self._sync()
        return [self._made[t] for t in doc["order"] if t in self._made]

    def get(self, tid: str) -> Tester | None:
        self._sync()
        return self._made.get(str(tid or "").strip())

    def pick(self, tid: str = "", chat: str = "") -> Tester:
        """The named tester; else the one this chat used last; else the default (Fortnite) one."""
        tid = str(tid or "").strip()
        if tid:
            found = self.get(tid)
            if found is None:
                names = ", ".join(t.id for t in self.list())
                raise LookupError(f"No tester {tid!r}. Open testers: {names}. Open one with playtester_open.")
            if chat:
                self._chat_pick[chat] = tid
            return found
        last = self._chat_pick.get(chat) if chat else None
        return (self.get(last) if last else None) or self.get(DEFAULT)  # type: ignore[return-value]

    # ------------------------------------------------------------------ open and close

    def open(self, target: Any, *, label: str = "", profile: str = "", view: str = "", name: str = "",
             keys: Any = None, player: str = "", settle_s: Any = None) -> Tester:
        spec = targets.parse(target)
        base = targets.make(spec)
        label = str(label or "").strip()[:48] or base.label
        # Jev hears the game's name: the one given, the profile's own (Fortnite, Roblox), else the tester's label.
        game = name or (base.profile.name if base.profile.name != "the game" else label.split("·")[-1].strip())
        prof = profiles.make(profile or base.profile.id, name=game, view=view, keys=keys)
        spec["profile"] = prof.to_dict()
        if player:
            spec["player"] = str(player).strip()
        if settle_s not in (None, ""):
            spec["settle_s"] = float(settle_s)
        with self._lock:
            doc = self._doc()
            if len(doc["items"]) >= MAX_TESTERS:
                raise ValueError(f"{MAX_TESTERS} testers are open: close one first (playtester_close).")
            if spec["kind"] == "fortnite" and any((i.get("spec") or {}).get("kind") == "fortnite" for i in doc["items"].values()):
                raise ValueError("The Fortnite client already has its tester: use tester='default'.")
            stem, n = slug(label), 1
            tid = stem
            while tid in doc["items"] or tid == DEFAULT:
                n += 1
                tid = f"{stem}-{n}"
            item = {"id": tid, "label": label, "spec": spec, "created": time.time()}
            tester = self._build(item)
            opener = getattr(tester.target, "open", None)
            if callable(opener):  # a web game opens its browser tab now
                opened = opener()
                if not opened.get("ok"):
                    raise ValueError(str(opened.get("error") or "Could not open the game."))
                item["spec"] = dict(tester.target.spec)
            doc["items"][tid] = item
            doc["order"].append(tid)
            self._save(doc)
            tester.doc = item
            self._made[tid] = tester
        return tester

    def close(self, tid: str) -> dict[str, Any]:
        tid = str(tid or "").strip()
        if tid == DEFAULT:
            return {"ok": False, "error": "The Fortnite tester can't be closed."}
        with self._lock:
            doc = self._doc()
            if tid not in doc["items"]:
                return {"ok": False, "error": f"No tester {tid!r}."}
            tester = self._made.get(tid) or self._build(doc["items"][tid])
            stopped = tester.pilot.stop("")
            try:
                tester.target.close()
            except Exception:
                pass
            doc["items"].pop(tid, None)
            doc["order"] = [i for i in doc["order"] if i != tid]
            self._save(doc)
            self._made.pop(tid, None)
            for chat, picked in list(self._chat_pick.items()):
                if picked == tid:
                    self._chat_pick.pop(chat, None)
        data = ScopedData(self.api.data, f"t.{tid}.")
        for prefix in ("run.", "sum.", "stop.", "lesson.", "memory.", "map."):
            for key in list((data.items(prefix) or {}).keys()):
                data.delete(key)
        data.delete("active")
        shutil.rmtree(self.api.local_dir(f"frames-{tid}"), ignore_errors=True)
        return {"ok": True, "closed": tid, "stopped": bool(stopped.get("stopped"))}

    def rename(self, tid: str, label: str) -> dict[str, Any]:
        tester = self.get(tid)
        label = str(label or "").strip()[:48]
        if tester is None or not label:
            return {"ok": False, "error": "Give an open tester and a name."}
        tester.doc["label"] = label
        self._save_one(tester.doc)
        return {"ok": True, "tester": tester.summary()}
