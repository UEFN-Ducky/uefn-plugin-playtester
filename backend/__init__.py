"""Play Tester: Jev plays games (Fortnite, Godot, Unreal, Roblox, web pages, any window) and shows every
move in its tab, one tab per tester.

A tester is one game (testers.py, tester.py). Tools, workflow nodes and the tab's panel calls all pick a
tester (``tester=``; empty = the one this chat used last, else the Fortnite one) and run its methods.
Nothing here touches a game until it is given something to do (a goal, keys, a target): empty calls
answer with what to give.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from . import capture, lease, record, targets, testers, windows
from .tester import BEACON_HOW, DEFAULT, Tester, to_bool, to_num

PLUGIN_ID = "playtester"
INTENT = (
    r"\b(play ?test(s|ing|er)?|play-?test\w*|in[- ]game|ingame|fortnite client|test (it |this |that |my |the )?"
    r"(island|map|level|game)|walk (the player|to)|move the (player|character)|jev)\b"
)

_MANAGER: testers.TesterManager | None = None


def _settings() -> dict[str, Any]:
    try:
        from frontend.ui_web.plugin_host_api import prefs_plugin_get

        return prefs_plugin_get(PLUGIN_ID) or {}
    except Exception:
        return {}


def _save_setting(key: str, value: Any) -> None:
    try:
        from frontend.ui_web.plugin_host_api import prefs_plugin_get, prefs_plugin_set

        slot = dict(prefs_plugin_get(PLUGIN_ID) or {})
        slot[key] = value
        prefs_plugin_set(PLUGIN_ID, slot)
    except Exception:
        pass


def _island() -> str:
    """The open project's name (the island runs are about); '' when Ducky has none."""
    try:
        import os

        from frontend.settings import PanelSettings

        root = PanelSettings.load().uefn_project_root or ""
        return os.path.basename(os.path.normpath(root)) if root else ""
    except Exception:
        return ""


def _push(event: dict[str, Any]) -> None:
    from frontend.ui_web.verse_editor.panel_events import push_agent_event

    push_agent_event(event)


def _chat() -> str:
    try:
        from backend.agent.hammer_guard import current_conversation

        return str(current_conversation() or "")
    except Exception:
        return ""


def register(api: Any) -> None:
    global _MANAGER
    mgr = testers.TesterManager(api, settings=_settings, save_setting=_save_setting, push=_push, island=_island,
                                recorder=record.MediaKitRecorder(api), lease=lease.InputLease(api.local_dir("lease")))
    _MANAGER = mgr

    def on(tester: Any, work: Callable[[Tester], dict[str, Any]]) -> dict[str, Any]:
        """Run ``work`` on the chosen tester; an unknown tester answers with the open ones."""
        try:
            t = mgr.pick(str(tester or ""), _chat())
        except LookupError as exc:
            return {"ok": False, "error": str(exc)}
        try:
            return work(t)
        except (ValueError, RuntimeError) as exc:
            return {"ok": False, "error": str(exc), "tester": t.id}

    def summaries() -> list[dict[str, Any]]:
        return [t.summary() for t in mgr.list()]

    def status(tester: Any = "") -> dict[str, Any]:
        out = on(tester, lambda t: t.status())
        out["testers"] = summaries()
        return out

    def target_list(thumbnails: Any = False) -> dict[str, Any]:
        rows = windows.games(windows.list_windows())
        if to_bool(thumbnails):
            for row in rows[:16]:
                row["thumb"] = capture.thumbnail(int(row["hwnd"]))
        return {"ok": True, "presets": targets.presets(), "windows": rows, "testers": summaries(),
                "how": "playtester_open(target=...) with a preset (fortnite, godot, unreal, roblox, web:<url>) "
                       "or window:<exe or title> from windows."}

    def open_tester(target: str = "", label: str = "", profile: str = "", view: str = "", game_name: str = "",
                    keys: str = "", player: str = "", settle_s: Any = None) -> dict[str, Any]:
        if not str(target or "").strip():
            return {"ok": False, "error": "Say what to test: fortnite, godot, unreal, roblox, web:<url> or window:<exe or title> "
                                          "(playtester_targets lists the open windows)."}
        try:
            t = mgr.open(target, label=label, profile=profile, view=view, name=game_name, keys=keys or None,
                         player=player, settle_s=settle_s)
        except (ValueError, LookupError) as exc:
            return {"ok": False, "error": str(exc)}
        chat = _chat()
        if chat:
            mgr._chat_pick[chat] = t.id
        st = t.status()
        return {"ok": True, "tester": t.summary(), "ready": st.get("ready"), "next": st.get("next"),
                "use": f"Pass tester={t.id!r} to playtester_run, playtester_check, playtester_press and the rest."}

    def close_tester(tester: str = "") -> dict[str, Any]:
        if not str(tester or "").strip():
            return {"ok": False, "error": "Say which tester to close (playtester_testers lists them)."}
        out = mgr.close(tester)
        out["testers"] = summaries()
        return out

    # ---------------------------------------------------------------- Fortnite's beacon

    def add_beacon() -> dict[str, Any]:
        """Write the beacon's Verse file into the open project (Ducky's project writer, so Changes can undo it)."""
        try:
            from backend.uefn_plugins.host import get_contributions
            from frontend.ui_web.project_files import create_project_folder, create_project_verse_file
        except Exception:
            return {"ok": False, "error": "This UEFN Ducky can't write project files from a plugin: update the app."}
        row = next((r for r in get_contributions().get("verse_templates") or []
                    if r.get("plugin_id") == PLUGIN_ID and r.get("id") == "playtester_beacon"), None)
        content = ((row or {}).get("files") or [{}])[0].get("content") or (row or {}).get("content") or ""
        if not content:
            return {"ok": False, "error": "The beacon template is missing from the plugin: reinstall Play Tester."}
        folder = "Content/Verse/PlayTester"
        try:
            folder = create_project_folder("Content/Verse", "PlayTester")["path"]
        except ValueError as exc:
            if "Already exists" not in str(exc):
                return {"ok": False, "error": f"Could not make the PlayTester folder: {exc}"}
        try:
            made = create_project_verse_file(folder, "playtester_beacon_device.verse", content)
        except ValueError as exc:
            if "Already exists" in str(exc):
                return {"ok": True, "added": False, "path": f"{folder}/playtester_beacon_device.verse",
                        "message": "The beacon is already in the project."}
            return {"ok": False, "error": f"Could not write the beacon file: {exc}"}
        return {"ok": True, "added": True, "path": made["path"]}

    def beacon_tool(add: Any = False) -> dict[str, Any]:
        fortnite = mgr.get(DEFAULT)
        out: dict[str, Any] = {"ok": True, "beacon": fortnite.beacon_state() if fortnite else None, "template": "playtester_beacon",
                               "file": "Content/Verse/PlayTester/playtester_beacon_device.verse", "how": BEACON_HOW}
        if to_bool(add):
            made = add_beacon()
            if not made.get("ok"):
                return {**out, "ok": False, "error": made["error"]}
            out["added"] = made
            out["next"] = ("Build Verse (workspace_compile_verse), push the changes, place playtester_beacon_device "
                           "in the level, then launch the session.")
        return out

    def level_map(tester: Any = "", refresh: Any = False) -> dict[str, Any]:
        def read(t: Tester) -> dict[str, Any]:
            out = t.level_map(to_bool(refresh))
            if out.get("actors") and len(out["actors"]) > 80:
                out["actors_shown"] = 80
                out["actors"] = out["actors"][:80]
            return out

        return on(tester or DEFAULT, read)

    # ---------------------------------------------------------------- MCP tools

    @api.tool(intent=INTENT, listener=False)
    def playtester_status(tester: str = "") -> dict:
        """Can Play Tester play test now: the game open and in play, which brain answers (TypeSafe key, Ducky AI or a gateway), the running test, and every open tester (testers)."""
        return status(tester)

    @api.tool(intent=INTENT, listener=False)
    def playtester_targets(thumbnails: bool = False) -> dict:
        """What can be play tested: presets (fortnite, godot, unreal, roblox, web:<url>) and the open game windows (window:<exe>), plus the open testers."""
        return target_list(thumbnails)

    @api.tool(intent=INTENT, listener=False)
    def playtester_open(target: str = "", label: str = "", profile: str = "", view: str = "", game_name: str = "",
                        keys: str = "", player: str = "", settle_s: float = 0) -> dict:
        """Open a tester for another game; it becomes a tab in Play Tester and its tester id goes to every other tool.

        target: fortnite | godot[:<project folder>] | unreal[:<exe>] | roblox | web:<url or localhost:port> |
        window:<exe or part of the title>. profile: how it is played: fps, tps, roblox, side2d (platformer),
        topdown, pointclick (default from the target). view: change just the view. game_name: what Jev calls the
        game. keys: the game's own keys for the moves, e.g. "SPACE=ARROWUP, E=F". player (Godot): the player node
        path for positions. Several testers run at once: desktop windows take turns pressing keys, Godot and web
        testers never wait.
        """
        return open_tester(target, label, profile, view, game_name, keys, player, settle_s or None)

    @api.tool(intent=INTENT, listener=False)
    def playtester_close(tester: str = "") -> dict:
        """Close a tester (and its tab): stops its run, closes its browser tab, forgets its runs. The Fortnite tester stays."""
        return close_tester(tester)

    @api.tool(intent=INTENT, listener=False)
    def playtester_testers() -> dict:
        """Every open tester: id, game, target, running run, waiting for its turn."""
        return {"ok": True, "testers": summaries()}

    @api.tool(intent=INTENT, listener=False)
    def playtester_run(goal: str = "", target: str = "", success: str = "", interact: bool = False,
                       max_steps: int = 0, max_seconds: float = 0, dry_run: bool = False, wait_s: float = 20,
                       target_actor: str = "", target_xyz: str = "", success_print: str = "", record: bool = False,
                       tester: str = "") -> dict:
        """Play test in game: Jev drives the player toward a goal about once a second and returns what happened.

        tester: which game (playtester_open gives the id; empty = the one this chat used last, else Fortnite).
        goal: what to do ("walk to the red button and press it"). target: the thing to walk to
        ("the red button"), found with Jev's Box. target_actor (Fortnite): the level actor's label, or target_xyz
        "x,y,z" in centimetres: with the beacon device in the island the player is steered by world
        coordinates too, so targets behind the player, behind buildings or on another floor are found.
        success: a yes/no check Jev answers on each frame ("Is the door open?"); the run succeeds when it
        holds twice. success_print (Fortnite): text a Verse Print shows when the test passed ("coin collected");
        the run passes the moment the island prints it. interact: hold the use key at the target. dry_run: decide
        and show moves without pressing anything. record: film the run with Island Media Kit (Fortnite). Waits up
        to wait_s (max 25) and returns the result or the run id to playtester_wait on.
        """
        return on(tester, lambda t: t.run(goal, target, success, interact, max_steps, max_seconds, dry_run, wait_s, "tool",
                                          target_actor, target_xyz, success_print, record))

    @api.tool(intent=INTENT, listener=False)
    def playtester_map(refresh: bool = False, tester: str = "") -> dict:
        """The Fortnite island's layout for play tests: every device's label and position (x, y, z) read from UEFN, plus landmarks the beacon prints."""
        return level_map(tester, refresh)

    @api.tool(intent=INTENT, listener=False)
    def playtester_where(target_actor: str = "", target_xyz: str = "", tester: str = "") -> dict:
        """Where the player is in the world and how far and which way a target is (Fortnite: the beacon; Godot: node positions, target_actor = a node path)."""
        return on(tester, lambda t: t.where(target_actor, target_xyz))

    @api.tool(intent=INTENT, listener=False)
    def playtester_beacon(add: bool = False) -> dict:
        """Check the Play Tester beacon device that reports the Fortnite player's world position; add=true puts its Verse file in the project."""
        return beacon_tool(add)

    @api.tool(intent=INTENT, listener=False)
    def playtester_wait(run_id: str = "", timeout_s: float = 20, tester: str = "") -> dict:
        """Wait (max 25 s) for a play test run to finish and return its result, steps and last frame."""
        return on(tester, lambda t: t.wait(run_id, timeout_s))

    @api.tool(intent=INTENT, listener=False)
    def playtester_stop(run_id: str = "", tester: str = "") -> dict:
        """Stop a tester's running play test (or a given run) after its current step; every key is released."""
        return on(tester, lambda t: t.stop(run_id))

    @api.tool(intent=INTENT, listener=False)
    def playtester_check(questions: dict | str = "", tester: str = "") -> dict:
        """Look at the game once with Jev: answer your own yes/no, choice, score or box questions about the current frame.

        questions: {id: {type: noul|choice|score|box, instructions, criteria?}}; call the frame `frame`,
        for example {"open": {"type": "noul", "instructions": "Is the door in `frame` open?"}}.
        """
        return on(tester, lambda t: t.check(questions))

    @api.tool(intent=INTENT, listener=False)
    def playtester_find(target: str = "", tester: str = "") -> dict:
        """Find something on the game's screen with Jev: is it visible, which side, and its box."""
        return on(tester, lambda t: t.find(target))

    @api.tool(intent=INTENT, listener=False)
    def playtester_press(keys: str = "", seconds: float = 0.5, tester: str = "") -> dict:
        """Press and hold keys in the game (W, A, S, D, SPACE, E, SHIFT, CTRL, digits, ARROWUP…) for 0.05-3 s; the tester's key map applies; shown in its tab."""
        return on(tester, lambda t: t.press(keys, seconds))

    @api.tool(intent=INTENT, listener=False)
    def playtester_look(dx_degrees: float = 0, dy_degrees: float = 0, tester: str = "") -> dict:
        """Turn the game's camera: dx_degrees right (negative = left), dy_degrees down (negative = up). In a side or top-down game it turns which way the player walks."""
        return on(tester, lambda t: t.look(dx_degrees, dy_degrees))

    @api.tool(intent=INTENT, listener=False)
    def playtester_click(button: str = "", seconds: float = 0.05, x: float = -1, y: float = -1, tester: str = "") -> dict:
        """Click or hold a mouse button in the game: left fires or uses, right aims. x and y (0..1 across and down the picture) click that point, for menus and point-and-click games."""
        at = (x, y) if x >= 0 and y >= 0 else (None, None)
        return on(tester, lambda t: t.click(button, seconds, *at))

    @api.tool(intent=INTENT, listener=False)
    def playtester_calibrate(turn_degrees: float = 0, tester: str = "") -> dict:
        """Measure the game's mouse speed for Play Tester turns: turns turn_degrees (try 20), finds the same spot with Jev, saves it for that tester."""
        return on(tester, lambda t: t.calibrate(turn_degrees))

    @api.tool(intent=INTENT, listener=False)
    def playtester_lessons(target: str = "", limit: int = 10, tester: str = "") -> dict:
        """What past play tests taught about a target: success rate, average steps, what blocked runs, where it was found, tips.

        Empty target: every target tested in this game with runs and wins. Read it before a playtester_run on a
        target that failed before; the next run of a target already starts from this memory.
        """
        return on(tester, lambda t: t.lessons(target, limit))

    @api.tool(intent=INTENT, listener=False)
    def playtester_get_run(run_id: str = "", from_step: int = 0, tester: str = "") -> dict:
        """Read a play test run step by step: Jev's answers, the keys and turns, the reason and timings (latest run when empty)."""
        return on(tester, lambda t: t.get_run(run_id, from_step))

    @api.tool(intent=INTENT, listener=False)
    def playtester_list_runs(limit: int = 10, tester: str = "") -> dict:
        """List a tester's recent play test runs with their goal, result, steps and time."""
        return on(tester, lambda t: t.list_runs(limit))

    @api.tool(intent=INTENT, listener=False)
    def playtester_frame(run_id: str = "", step: int = 0, tester: str = "") -> dict:
        """Get the saved frame of a play test step as an image (last step of the latest run when empty)."""
        return on(tester, lambda t: t.frame(run_id, step))

    # ---------------------------------------------------------------- panel calls (every one takes tester)

    def open_panes() -> set[str]:
        return {str((t.target.spec or {}).get("pane") or "") for t in mgr.list() if t.target.kind == "web"} - {""}

    def rpc_web_op(op: str = "", pane: str = "", args: Any = None) -> dict[str, Any]:
        """A browser tester's page work in Ducky's window; only for panes Play Tester opened itself."""
        if str(pane or "") not in open_panes():
            return {"ok": False, "error": "That browser tab isn't a Play Tester tab."}
        return targets.web_op(op, pane, args if isinstance(args, dict) else {})

    rpc = api.register_panel_rpc
    rpc("status", lambda tester="": status(tester))
    rpc("testers", lambda: {"ok": True, "testers": summaries()})
    rpc("targets", lambda thumbnails=True: target_list(thumbnails))
    rpc("open", lambda target="", label="", profile="", view="", game_name="", keys="", player="":
        open_tester(target, label, profile, view, game_name, keys, player))
    rpc("close", lambda tester="": close_tester(tester))
    rpc("rename", lambda tester="", label="": mgr.rename(tester, label))
    def set_brain(t: Tester, pick: str, brain: str) -> dict[str, Any]:
        out = t.set_brain(pick, brain)
        return t.status() if out.get("ok") else out

    rpc("set_brain", lambda tester="", pick="", brain="": on(tester, lambda t: set_brain(t, pick, brain)))
    rpc("run", lambda goal="", target="", success="", interact=False, dry_run=False, max_steps=0, target_actor="",
        target_xyz="", success_print="", tester="":
        on(tester, lambda t: t.run(goal, target, success, interact, max_steps, 0, dry_run, 0, "tab", target_actor, target_xyz,
                                   success_print)))
    rpc("stop", lambda run_id="", tester="": on(tester, lambda t: t.stop(run_id)))
    rpc("get_run", lambda run_id="", from_step=0, tester="": on(tester, lambda t: t.get_run(run_id, from_step)))
    rpc("list_runs", lambda limit=15, tester="": on(tester, lambda t: t.list_runs(limit)))
    rpc("frame", lambda run_id="", step=0, width=0, tester="": on(tester, lambda t: t.rpc_frame(run_id, step, width)))
    rpc("press", lambda keys="", seconds=0.3, tester="": on(tester, lambda t: t.press(keys, seconds)))
    rpc("look", lambda dx_degrees=0, dy_degrees=0, tester="": on(tester, lambda t: t.look(dx_degrees, dy_degrees)))
    rpc("web_op", rpc_web_op)

    # ---------------------------------------------------------------- workflow nodes (config tester picks the game)

    def _cfg(ctx: dict[str, Any]) -> dict[str, Any]:
        return ctx.get("config") if isinstance(ctx.get("config"), dict) else {}

    def _inputs(ctx: dict[str, Any]) -> dict[str, Any]:
        return ctx.get("inputs") if isinstance(ctx.get("inputs"), dict) else {}

    def _tester(ctx: dict[str, Any]) -> str:
        return str(_inputs(ctx).get("tester") or _cfg(ctx).get("tester") or "")

    @api.register_pipeline_node("playtester.status")
    def node_status(ctx: dict) -> dict:
        out = status(_tester(ctx))
        return {**out, "ready": bool(out.get("ready")), "next": out.get("next", out.get("error", ""))}

    @api.register_pipeline_node("playtester.open")
    def node_open(ctx: dict) -> dict:
        cfg, ins = _cfg(ctx), _inputs(ctx)
        want = str(ins.get("target") or cfg.get("target") or "")
        label = str(cfg.get("label") or "")
        existing = next((t for t in mgr.list() if label and t.label == label), None)
        if existing is not None:  # a workflow run again reuses its tester
            return {"ok": True, "tester": existing.id, "summary": f"{existing.label} is open"}
        out = open_tester(want, label, str(cfg.get("profile") or ""), str(cfg.get("view") or ""),
                          str(cfg.get("game_name") or ""), str(cfg.get("keys") or ""), str(cfg.get("player") or ""))
        tid = (out.get("tester") or {}).get("id") if out.get("ok") else ""
        return {**out, "tester": tid or "", "summary": out.get("next") or out.get("error", "")}

    @api.register_pipeline_node("playtester.check")
    def node_check(ctx: dict) -> dict:
        cfg, ins = _cfg(ctx), _inputs(ctx)
        out = on(_tester(ctx), lambda t: t.check(ins.get("questions") or cfg.get("questions")))
        return {**out, "answers": out.get("answers") or {}}

    @api.register_pipeline_node("playtester.run")
    def node_run(ctx: dict) -> dict:
        cfg, ins = _cfg(ctx), _inputs(ctx)
        goal = str(ins.get("goal") or cfg.get("goal") or "")

        def play(t: Tester) -> dict[str, Any]:
            out = t.run(goal, str(cfg.get("target") or ""), str(cfg.get("success") or ""), cfg.get("interact"),
                        cfg.get("max_steps"), 0, cfg.get("dry_run"), 0, "workflow",
                        str(cfg.get("target_actor") or ""), str(cfg.get("target_xyz") or ""), str(cfg.get("success_print") or ""))
            if not out.get("ok"):
                return {**out, "status": "failed", "summary": out.get("error", "")}
            rid = out["run_id"]
            result = t.wait(rid, 25)
            from .pilot import LIMIT_SECONDS

            deadline = time.time() + LIMIT_SECONDS + 30
            while result.get("ok") and not result.get("finished") and time.time() < deadline:
                result = t.wait(rid, 25)
            return {**result, "status": result.get("status"), "steps": result.get("n_steps"),
                    "seconds": result.get("seconds"), "summary": result.get("reason") or result.get("last_why", ""),
                    "frame": result.get("frame", "")}

        return on(_tester(ctx), play)

    @api.register_pipeline_node("playtester.press")
    def node_press(ctx: dict) -> dict:
        cfg = _cfg(ctx)
        return on(_tester(ctx), lambda t: t.press(cfg.get("keys") or "", to_num(cfg.get("seconds"), 0.5)))

    @api.register_pipeline_node("playtester.look")
    def node_look(ctx: dict) -> dict:
        cfg = _cfg(ctx)
        return on(_tester(ctx), lambda t: t.look(to_num(cfg.get("dx_degrees")), to_num(cfg.get("dy_degrees"))))

    @api.register_pipeline_node("playtester.stop")
    def node_stop(ctx: dict) -> dict:
        return on(_tester(ctx), lambda t: t.stop(str(_cfg(ctx).get("run_id") or "")))

    @api.register_pipeline_node("playtester.map")
    def node_map(ctx: dict) -> dict:
        out = level_map(_tester(ctx), _cfg(ctx).get("refresh"))
        return {**out, "count": out.get("count", 0), "summary": f"{out.get('count', 0)} devices" if out.get("ok") else out.get("error", "")}

    @api.register_pipeline_node("playtester.lessons")
    def node_lessons(ctx: dict) -> dict:
        cfg = _cfg(ctx)
        out = on(_tester(ctx), lambda t: t.lessons(str(cfg.get("target") or ""), cfg.get("limit") or 10))
        return {**out, "summary": "; ".join(out.get("tips") or []) or f"{out.get('runs', 0)} runs", "success_rate": out.get("success_rate")}

    @api.register_pipeline_node("playtester.where")
    def node_where(ctx: dict) -> dict:
        cfg = _cfg(ctx)
        out = on(_tester(ctx), lambda t: t.where(str(cfg.get("target_actor") or ""), str(cfg.get("target_xyz") or "")))
        return {**out, "say": out.get("say") or out.get("error", ""), "distance_m": (out.get("rel") or {}).get("distance_m")}

    api.log("Play Tester tools, nodes and panel calls registered")
