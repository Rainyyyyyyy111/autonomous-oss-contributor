from __future__ import annotations

import json
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from conftest import BASE, FIX, FixtureSandbox
from pydantic import ValidationError
from test_desktop import handoff
from test_protocol_cli_store import rpc

from oss_contributor.codex import Codex
from oss_contributor.config import Config, Limits
from oss_contributor.desktop import Desktop
from oss_contributor.models import Repair
from oss_contributor.process import ContributorError
from oss_contributor.tool_guard import (
    ExecutionLimitExceeded,
    ToolGuard,
    ToolPolicy,
    ToolState,
    fingerprint,
)
from oss_contributor.worker_tools import WorkerTools


def test_repeated_calls_are_canonical_and_detect_interleaving():
    guard = ToolGuard(ToolPolicy(max_repeated_calls=2))
    calls = []
    for _ in range(2):
        for name in ("repo_read", "repo_search"):
            guard.run(name, {"b": 2, "a": 1}, lambda name=name: calls.append(name) or "evidence", lambda: BASE)
    with pytest.raises(ExecutionLimitExceeded, match="repeated"):
        guard.run("repo_read", {"a": 1, "b": 2}, lambda: calls.append("forbidden"), lambda: BASE)
    assert len(calls) == guard.state.steps == 4


def test_different_arguments_cannot_make_identical_results_progress():
    guard = ToolGuard(ToolPolicy(max_no_progress=2))
    for path in ("one", "two"):
        guard.run("repo_read", {"path": path}, lambda: "same evidence", lambda: BASE)
    with pytest.raises(ExecutionLimitExceeded, match="no-progress"):
        guard.run("repo_read", {"path": "three"}, lambda: "same evidence", lambda: BASE)
    assert guard.state.steps == 3


def test_total_step_cap_applies_even_when_every_call_makes_progress():
    guard = ToolGuard(ToolPolicy(max_steps=2))
    for value in (1, 2):
        guard.run("repo_read", {"path": value}, lambda value=value: value, lambda: BASE)
    with pytest.raises(ExecutionLimitExceeded, match="total step"):
        guard.run("repo_read", {"path": 3}, lambda: pytest.fail("must not execute"), lambda: BASE)
    assert guard.state.steps == 2


def test_changing_errors_and_command_duration_are_not_progress():
    guard = ToolGuard(ToolPolicy(max_no_progress=2))
    guard.run("sandbox_exec", {"argv": ["test"]},
              lambda: {"code": 0, "output": "same", "seconds": 1}, lambda: BASE)
    guard.run("sandbox_exec", {"argv": ["test"]},
              lambda: {"code": 0, "output": "same", "seconds": 2}, lambda: BASE)
    with pytest.raises(ExecutionLimitExceeded, match="no-progress"):
        guard.run("sandbox_exec", {"argv": ["test"]},
                  lambda: {"code": 1, "output": "a new error", "seconds": 3}, lambda: BASE)


def test_failed_calls_count_and_a_limit_is_sticky():
    guard = ToolGuard(ToolPolicy(max_no_progress=2))

    def fail():
        raise ValueError("different error text is not evidence")

    with pytest.raises(ValueError):
        guard.run("invalid", {}, fail, lambda: BASE)
    with pytest.raises(ExecutionLimitExceeded, match="no-progress"):
        guard.run("invalid", {"different": True}, fail, lambda: BASE)
    with pytest.raises(ExecutionLimitExceeded):
        guard.run("repo_read", {}, lambda: "new", lambda: {"new": "workspace"})
    assert guard.state.steps == 2


def test_real_edit_allows_reread_but_noops_and_oscillation_do_not_reset_idle():
    files = dict(BASE)
    guard = ToolGuard(ToolPolicy(max_repeated_calls=1, max_no_progress=3))
    guard.run("repo_read", {"path": "calc.py"}, lambda: files["calc.py"], lambda: files)
    guard.run("repo_apply", {"changes": {"calc.py": FIX}},
              lambda: files.update({"calc.py": FIX}), lambda: files)
    guard.run("repo_read", {"path": "calc.py"}, lambda: files["calc.py"], lambda: files)
    guard.run("repo_apply", {"changes": BASE}, lambda: files.update(BASE), lambda: files)
    guard.run("repo_apply", {"changes": {}}, lambda: None, lambda: files)
    with pytest.raises(ExecutionLimitExceeded, match="no-progress"):
        guard.run("repo_apply", {"changes": {"noop": None}}, lambda: None, lambda: files)


def test_restart_cannot_refund_a_reserved_or_incomplete_call():
    saved = []
    guard = ToolGuard(ToolPolicy(), save=lambda state: saved.append(state.model_dump_json()))
    guard.begin("repo_read", {"path": "private fixture"}, fingerprint(BASE))
    assert "private fixture" not in saved[-1]
    restored = ToolGuard(ToolPolicy(), ToolState.model_validate_json(saved[-1]))
    with pytest.raises(ExecutionLimitExceeded, match="incomplete"):
        restored.begin("repo_read", {}, fingerprint(BASE))
    assert restored.state.steps == 1


@pytest.mark.parametrize("field", ["max_steps", "max_repeated_calls", "max_no_progress"])
def test_limits_are_validated_and_old_configuration_gets_defaults(field):
    assert Limits().tools == ToolPolicy()
    with pytest.raises(ValidationError):
        ToolPolicy(**{field: 0})


def scripted_codex(config, monkeypatch, messages):
    codex = Codex(config)
    closed = []

    @contextmanager
    def session(deadline, handler):
        client, process = rpc([{"id": 1, "result": {}}, *messages], handler)
        try:
            yield client, "/synthetic"
        finally:
            closed.append(True)

    monkeypatch.setattr(codex, "_session", session)
    monkeypatch.setattr(codex, "_thread", lambda *_: "fake-thread")
    return codex, closed


def call(index, name="repo_read", **arguments):
    return {"id": index, "method": "item/tool/call",
            "params": {"tool": name, "arguments": arguments or {"path": "calc.py"}}}


def test_rpc_does_not_return_a_guard_limit_as_a_retriable_tool_error(monkeypatch):
    config = Config(limits=Limits(tools=ToolPolicy(max_repeated_calls=2)))
    codex, closed = scripted_codex(config, monkeypatch, [call(i) for i in range(3)])
    with FixtureSandbox(config.repositories, BASE, time.monotonic() + 10) as box:
        with pytest.raises(ExecutionLimitExceeded, match="repeated"):
            codex.ask(Repair, "fixture", box, time.monotonic() + 10)
    assert closed == [True]


def test_standalone_steps_accumulate_across_model_stages(monkeypatch):
    final = {"method": "turn/completed", "params": {"turn": {
        "status": "completed", "items": [{"type": "agentMessage", "text": json.dumps({
            "summary": "fixture", "root_cause": "fixture", "evidence": ["fixture"],
        })}],
    }}}
    config = Config()
    codex, _ = scripted_codex(config, monkeypatch, [call(1), final])
    guard = ToolGuard(ToolPolicy(max_steps=2))
    with FixtureSandbox(None, BASE, time.monotonic() + 10) as box, codex.guarded(guard):
        for _ in range(2):
            codex.ask(Repair, "fixture", box, time.monotonic() + 10)
        with pytest.raises(ExecutionLimitExceeded, match="total step"):
            codex.ask(Repair, "fixture", box, time.monotonic() + 10)
    assert codex.tool_guard is None


def native(rig, **limits):
    rig[0].limits.tools = ToolPolicy(**limits)
    desktop = Desktop(rig[4])
    action = desktop.tick()
    desktop.attach(action["ticket"], "guarded-native")
    return desktop, action


def toolbox(rig, action):
    return WorkerTools(action["job_path"], github=rig[2], sandbox_factory=FixtureSandbox)


def test_native_limit_is_durable_cancels_and_cannot_publish_even_with_handoff(rig):
    desktop, action = native(rig, max_repeated_calls=2)
    reserved = rig[1].usage()["model_seconds"]
    with toolbox(rig, action) as worker:
        for _ in range(2):
            worker.tool("repo_read", {"path": "calc.py"})
        with pytest.raises(ExecutionLimitExceeded, match="repeated"):
            worker.tool("repo_read", {"path": "calc.py"})
    job = rig[1].desktop_job(action["ticket"])
    assert job["state"] == "cancelling"
    assert job["data"]["tool_activity"]["steps"] == 3
    assert rig[1].usage()["model_seconds"] == reserved
    assert desktop.tick()["action"] == "cancel"
    rig[1].pause("operator pause")
    rig[1].set_meta("paused", "")
    assert desktop.tick()["action"] == "cancel"
    path = Path(action["handoff_path"])
    path.write_text(handoff(action).model_dump_json())
    assert desktop.finish(action["ticket"], path)["action"] == "cancel"
    assert not rig[2].published and not rig[3].calls
    desktop.confirm_stopped(action["ticket"], "guarded-native")
    assert not rig[1].desktop_jobs()
    assert rig[1].get(action["task_id"])["status"] == "failed"


def test_native_restart_cannot_reset_steps_or_pinned_policy(rig):
    desktop, action = native(rig, max_steps=4)
    for _ in range(2):
        with toolbox(rig, action) as worker:
            worker.tool("repo_read", {"path": "calc.py"})
    rig[0].limits.tools.max_steps = 1000
    with pytest.raises(ExecutionLimitExceeded, match="total step"), toolbox(rig, action):
        pytest.fail("opening a third workspace must be denied")
    assert rig[1].desktop_job(action["ticket"])["data"]["tool_activity"]["steps"] == 4
    assert desktop.tick()["action"] == "cancel"


def test_native_no_progress_detects_changing_queries_and_bad_arguments(rig):
    desktop, action = native(rig, max_no_progress=2)
    with toolbox(rig, action) as worker:
        worker.tool("repo_search", {"pattern": "never-matches-1"})
        with pytest.raises(ExecutionLimitExceeded, match="no-progress"):
            worker.tool("repo_search", {"pattern": "never-matches-2"})
    assert desktop.status()["active_jobs"][0]["tool_activity"]["blocked"]
    assert not rig[2].published


def test_native_rejects_incomplete_work_without_repeating_it(rig):
    desktop, action = native(rig)
    job = rig[1].desktop_job(action["ticket"])
    job["data"]["tool_activity"].update(steps=1, pending=True)
    rig[1].save_desktop_job(job)
    with pytest.raises(ExecutionLimitExceeded, match="incomplete"), toolbox(rig, action):
        pytest.fail("an interrupted reserved call must not be retried")
    assert desktop.tick()["action"] == "cancel"


def test_native_missing_monitoring_fails_closed(rig):
    desktop, action = native(rig)
    desktop.confirm_stopped(action["ticket"], "guarded-native")
    path = Path(action["handoff_path"])
    path.write_text(handoff(action).model_dump_json())
    result = desktop.finish(action["ticket"], path)
    assert "missing" in result["reason"] and not rig[2].published


def test_native_assigned_regression_is_immutable_and_counts_edits(rig):
    from conftest import REGRESSION

    _, action = native(rig)
    with toolbox(rig, action) as worker:
        worker.tool("regression_write", {"content": REGRESSION})
        worker.tool("repo_apply", {"changes": {"calc.py": FIX}})
        assert worker.snapshot()["calc.py"] == FIX
        with pytest.raises(ContributorError, match="unchanged"):
            worker.tool("regression_write", {"content": REGRESSION + "\n# change\n"})
    state = rig[1].desktop_job(action["ticket"])["data"]["tool_activity"]
    assert state["steps"] == 5 and not state["pending"]
    assert rig[1].desktop_job(action["ticket"])["state"] == "cancelling"


def test_legacy_native_handoff_is_not_given_fabricated_tool_evidence(rig):
    desktop, action = native(rig)
    job = rig[1].desktop_job(action["ticket"])
    del job["data"]["tool_policy"]
    del job["data"]["tool_activity"]
    rig[1].save_desktop_job(job)
    desktop.confirm_stopped(action["ticket"], "guarded-native")
    path = Path(action["handoff_path"])
    path.write_text(handoff(action).model_dump_json())
    desktop.finish(action["ticket"], path)
    assert rig[1].get(action["task_id"])["status"] == "open"
    assert "tool_activity" not in rig[1].desktop_job(action["ticket"])["data"]


@pytest.mark.parametrize("restriction", ["paused", "stopped", "scope"])
def test_native_toolbox_checks_authority_before_repository_access(rig, restriction):
    _, action = native(rig)
    if restriction == "paused":
        rig[1].set_meta("paused", "operator pause")
    elif restriction == "stopped":
        job = rig[1].desktop_job(action["ticket"])
        job["data"]["thread_stopped"] = True
        rig[1].save_desktop_job(job)
    else:
        rig[1].set_meta("desktop_scope", json.dumps({"mode": "monitor", "targets": []}))
    before = FixtureSandbox.created
    with pytest.raises(ContributorError), toolbox(rig, action):
        pytest.fail("repository access must be denied")
    assert FixtureSandbox.created == before
    assert rig[1].desktop_job(action["ticket"])["data"]["tool_activity"]["steps"] == 0


def test_standalone_engine_retains_guard_reason_and_never_publishes(rig, monkeypatch):
    from oss_contributor.engine import Engine

    rig[0].limits.tools.max_repeated_calls = 2
    codex, _ = scripted_codex(rig[0], monkeypatch, [call(i) for i in range(3)])
    engine = Engine(rig[0], rig[1], rig[2], codex, FixtureSandbox)
    ident = rig[1].create_task(rig[2].issue_value)
    engine.process(rig[1].get(ident))
    task = rig[1].get(ident)
    assert task["status"] == "failed"
    assert task["data"]["tool_activity"]["steps"] == 2
    assert "repeated" in task["data"]["reason"]
    assert not rig[2].published


def test_snapshot_failure_aborts_before_a_tool_can_loop():
    guard = ToolGuard(ToolPolicy())

    def broken():
        raise ContributorError("invalid sandbox evidence")

    with pytest.raises(ExecutionLimitExceeded, match="observation unavailable"):
        guard.run("repo_read", {}, lambda: pytest.fail("must not execute"), broken)
    assert guard.state.blocked and guard.state.steps == 0


def test_failed_command_cannot_claim_progress_by_mutating_files():
    files = dict(BASE)
    guard = ToolGuard(ToolPolicy(max_no_progress=1))

    def failed_command():
        files["calc.py"] = FIX
        return {"code": 1, "output": "new failure"}

    with pytest.raises(ExecutionLimitExceeded, match="no-progress"):
        guard.run("sandbox_exec", {"argv": ["test"]}, failed_command, lambda: files)


def test_native_snapshot_failure_persists_cancellation(rig, monkeypatch):
    desktop, action = native(rig)
    with toolbox(rig, action) as worker:
        def broken():
            raise ContributorError("invalid workspace")

        monkeypatch.setattr(worker.box, "snapshot", broken)
        with pytest.raises(ExecutionLimitExceeded, match="observation unavailable"):
            worker.tool("sandbox_exec", {"argv": ["python", "-c", "pass"]})
    job = rig[1].desktop_job(action["ticket"])
    assert job["state"] == "cancelling" and job["data"]["tool_activity"]["pending"]
    assert desktop.tick()["action"] == "cancel"
