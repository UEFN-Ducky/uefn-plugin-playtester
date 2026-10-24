"""record=true films a run through Island Media Kit's imk_record_start / imk_record_stop tools."""

from __future__ import annotations

from uefn_plugin_playtester import pilot, record, runs

import test_pilot as tp


class FakeApi:
    """api.call_tool standing in for an installed Media Kit; notes the run's step count at each call."""

    def __init__(self, store: runs.Runs) -> None:
        self.store, self.calls = store, []

    def call_tool(self, name, arguments):
        latest = self.store.latest()
        self.calls.append((name, arguments, int((latest or {}).get("n_steps") or 0)))
        if name == "imk_record_start":
            return {"ok": True, "record": {"id": "rec1", "state": "recording"}}
        return {"ok": True, "record": {"id": "rec1"}, "file": "C:/clips/run.mp4", "bytes": 1234}


def make(tmp_path, api):
    store = runs.Runs(tp.FakeData(), str(tmp_path / "frames"))
    rec = record.MediaKitRecorder(api(store) if callable(api) else api)
    the = pilot.Pilot(store, tp.ScriptJev(), game=tp.FakeGame(), push=lambda e: None,
                      settings=lambda: {"max_steps": "30"}, recorder=rec)
    return the, store, rec.api


def test_record_starts_before_the_run_stops_after_and_names_the_clip(tmp_path):
    the, store, api = make(tmp_path, FakeApi)
    out = the.start(goal="walk to the button", target="the button", record=True)
    assert out["ok"] and out["recording"] is True
    run = tp.wait_done(the, out["run_id"])
    (start, start_args, steps_at_start), (stop, stop_args, steps_at_stop) = api.calls
    assert (start, stop) == ("imk_record_start", "imk_record_stop")
    assert start_args["target"] == "fortnite" and steps_at_start == 0  # before the first move
    assert stop_args == {"record_id": "rec1"} and steps_at_stop == run["n_steps"] > 0  # after the last one
    assert run["clip_id"] == "rec1" and run["clip_file"] == "C:/clips/run.mp4"
    assert the.result(out["run_id"])["clip_id"] == "rec1"


def test_without_media_kit_the_run_plays_unrecorded(tmp_path):
    class NoMediaKit:
        def call_tool(self, name, arguments):
            raise ValueError("Unknown tool: imk_record_start")

    for api in (NoMediaKit(), object()):  # Media Kit missing; host without call_tool
        the, store, _ = make(tmp_path, api)
        out = the.start(goal="walk to the button", target="the button", record=True)
        assert out["ok"] and out["recording"] is False
        run = tp.wait_done(the, out["run_id"])
        assert run["status"] in runs.DONE and not run.get("clip_id") and not run.get("clip_error")


def test_no_record_flag_never_calls_media_kit(tmp_path):
    the, store, api = make(tmp_path, FakeApi)
    out = the.start(goal="walk to the button", target="the button")
    tp.wait_done(the, out["run_id"])
    assert api.calls == [] and "recording" not in out
