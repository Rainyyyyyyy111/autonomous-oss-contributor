from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from test_accounting import observation
from test_desktop import complete
from typer.testing import CliRunner

from oss_contributor.accounting import MeteringPolicy
from oss_contributor.cli import app
from oss_contributor.desktop import Desktop
from oss_contributor.errors import ContributorError


def test_foreign_usage_cannot_change_the_budget_or_cancel_an_authorized_job(rig):
    desktop = Desktop(rig[4])
    action = desktop.tick()
    desktop.attach(action["ticket"], "thread-1")
    before = rig[1].desktop_job(action["ticket"])
    path = Path(action["job_path"]).parent / "usage-observation.json"
    path.write_text(observation(thread_id="foreign-thread").model_dump_json())
    with pytest.raises(ContributorError, match="another native chat"):
        desktop.record_usage(action["ticket"], path)
    assert rig[1].desktop_job(action["ticket"]) == before
    assert desktop.accounting_report()["unmetered_calls"] == 1


def test_accounting_report_does_not_leak_revoked_or_foreign_monitor_tasks(rig):
    desktop = Desktop(rig[4])
    action = desktop.tick()
    assert desktop.accounting_report()["calls"]
    rig[0].repositories.clear()
    assert not desktop.accounting_report()["calls"]
    assert rig[1].desktop_jobs()[0]["ticket"] == action["ticket"]


def test_cost_limit_without_model_price_cannot_be_treated_as_zero(rig, tmp_path):
    rig[0].metering = MeteringPolicy(max_estimated_usd_per_call="1")
    desktop = Desktop(rig[4])
    action = desktop.tick()
    desktop.attach(action["ticket"], "thread-1")
    path = Path(action["job_path"]).parent / "usage-observation.json"
    path.write_text(observation().model_dump_json())
    desktop.record_usage(action["ticket"], path)
    result, _ = complete(desktop, action, tmp_path)
    assert "price unavailable" in result["reason"]
    assert not rig[2].published


def test_usage_counter_replay_cannot_clear_an_exceeded_limit(rig):
    rig[0].metering.max_observed_tokens_per_call = 100
    desktop = Desktop(rig[4])
    action = desktop.tick()
    desktop.attach(action["ticket"], "thread-1")
    path = Path(action["job_path"]).parent / "usage-observation.json"
    path.write_text(observation().model_dump_json())
    desktop.record_usage(action["ticket"], path)
    path.write_text(observation("old", input_tokens=50, cached_input_tokens=10,
                                output_tokens=5, reasoning_output_tokens=2).model_dump_json())
    assert desktop.record_usage(action["ticket"], path)["action"] == "cancel"
    assert rig[1].usage()["model_seconds"] == 1500


def test_worker_cannot_spend_finalization_time_on_more_tools(rig, monkeypatch):
    from conftest import FixtureSandbox

    from oss_contributor.worker_tools import WorkerTools

    desktop = Desktop(rig[4])
    action = desktop.tick()
    desktop.attach(action["ticket"], "thread-1")
    monkeypatch.setattr(time, "time", lambda: action["worker_expires"] + 1)
    with pytest.raises(ContributorError, match="not running"), WorkerTools(
        action["job_path"], github=rig[2], sandbox_factory=FixtureSandbox,
    ):
        pytest.fail("worker used its finalization reserve")
    assert desktop.tick()["action"] == "cancel"


def test_usage_and_memory_reports_are_offline_and_read_only(rig, monkeypatch):
    from oss_contributor import cli

    desktop = Desktop(rig[4])
    action = desktop.tick()
    before = rig[1].desktop_job(action["ticket"])
    monkeypatch.setattr(cli, "load", lambda path: rig[0])
    for args in (["accounting"], ["learning", "audit"]):
        result = CliRunner().invoke(app, args)
        assert result.exit_code == 0, result.output
        json.loads(result.output)
    assert rig[1].desktop_job(action["ticket"]) == before
    assert not rig[3].calls and not rig[2].published


def test_accounting_creation_failure_cannot_leak_a_model_time_reservation(rig, monkeypatch):
    def disk_full(*args, **kwargs):
        raise OSError("synthetic accounting creation failure")

    monkeypatch.setattr(rig[4].accounting, "start", disk_full)
    ident = rig[1].create_task(rig[2].issue_value)
    rig[4].process(rig[1].get(ident))
    assert rig[1].usage()["model_seconds"] < 1
    assert not rig[3].calls and not rig[2].published


def test_accounting_finalization_failure_still_refunds_once(rig, monkeypatch):
    def disk_full(*args, **kwargs):
        raise OSError("synthetic accounting finalization failure")

    monkeypatch.setattr(rig[4].accounting, "finish", disk_full)
    ident = rig[1].create_task(rig[2].issue_value)
    rig[4].process(rig[1].get(ident))
    assert rig[1].usage()["model_seconds"] < 1
    assert not rig[2].published


def test_implementation_fingerprint_covers_store_code_without_reading_private_links(tmp_path):
    from oss_contributor.accounting import implementation_digest

    package = tmp_path / "package"
    package.mkdir()
    source = package / "store.py"
    source.write_text("counter = 1\n")
    secret = tmp_path / "secret.txt"
    secret.write_text("private fixture")
    (package / "linked.py").symlink_to(secret)
    before = implementation_digest(package)
    secret.write_text("changed private fixture")
    assert implementation_digest(package) == before
    source.write_text("counter = 2\n")
    assert implementation_digest(package) != before


def test_store_reservation_is_atomic_across_midnight(rig, monkeypatch):
    days = iter(["2026-11-01", "2026-11-02"])
    monkeypatch.setattr(rig[1], "day", lambda: next(days))
    assert rig[1].reserve("model_seconds", 10)
    rig[1].refund_model("2026-11-01", 4)
    assert rig[1].usage("2026-11-01")["model_seconds"] == 6
    assert rig[1].usage("2026-11-02")["model_seconds"] == 0
    assert rig[1].usage()["day"] == "2026-11-02"


def test_standalone_ledger_and_refund_use_the_reserved_day(rig, monkeypatch):
    from oss_contributor.models import Assessment

    store, engine = rig[1], rig[4]
    day = "2026-11-01"
    monkeypatch.setattr(store, "day", lambda: day)
    reserve = store.reserve

    def cross_midnight(*args, **kwargs):
        nonlocal day
        result = reserve(*args, **kwargs)
        day = "2026-11-02"
        return result

    monkeypatch.setattr(store, "reserve", cross_midnight)
    engine.model(Assessment, "synthetic midnight test", None, time.monotonic() + 10)
    assert engine.accounting.report()["calls"][0]["day"] == "2026-11-01"
    assert 0 <= store.usage("2026-11-01")["model_seconds"] < 1
    assert store.usage("2026-11-02")["model_seconds"] == 0


def test_native_dispatch_rechecks_and_pins_budget_day_through_settlement(rig, monkeypatch):
    store, gh = rig[1], rig[2]
    desktop = Desktop(rig[4])
    ident = store.create_task(gh.issue_value)
    day = "2026-11-01"
    monkeypatch.setattr(store, "day", lambda: day)
    assert store.reserve("model_seconds", rig[0].limits.model_seconds_per_day - 1)
    base = gh.base

    def cross_midnight(*args):
        nonlocal day
        day = "2026-11-02"
        return base(*args)

    monkeypatch.setattr(gh, "base", cross_midnight)
    job = desktop.dispatch(store.get(ident))
    assert job is not None
    assert job["data"]["budget_day"] == "2026-11-02"
    assert job["data"]["reserved_seconds"] == 1500  # refreshed after preflight's one second
    day = "2026-11-03"
    desktop.job_result(job)
    assert desktop.accounting_report()["calls"][0]["day"] == "2026-11-02"
    desktop.attach(job["ticket"], "thread-midnight")
    store.pause("synthetic midnight cancellation")
    desktop.confirm_stopped(job["ticket"], "thread-midnight")
    assert 0 <= store.usage("2026-11-02")["model_seconds"] < 1
    assert store.usage("2026-11-01")["model_seconds"] == rig[0].limits.model_seconds_per_day - 1
    assert store.usage("2026-11-03")["model_seconds"] == 0


def test_publication_reservation_metadata_uses_the_actual_reserved_day(rig, monkeypatch):
    from test_engine import run_issue

    store = rig[1]
    day = "2026-11-01"
    monkeypatch.setattr(store, "day", lambda: day)
    reserve = store.reserve

    def cross_midnight(kind, *args, **kwargs):
        nonlocal day
        result = reserve(kind, *args, **kwargs)
        if kind == "publications":
            day = "2026-11-02"
        return result

    monkeypatch.setattr(store, "reserve", cross_midnight)
    task = run_issue(rig)
    assert task["status"] == "open", task["data"].get("reason")
    assert task["data"]["publication_reserved"] == "2026-11-01"
    assert store.usage("2026-11-01")["publications"] == 1
    assert store.usage("2026-11-02")["publications"] == 0
