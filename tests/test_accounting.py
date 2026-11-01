from __future__ import annotations

import json
import time
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import pytest
from conftest import BASE, FixtureSandbox
from pydantic import ValidationError
from test_desktop import complete, handoff, monitored_read
from test_protocol_cli_store import rpc

from oss_contributor.accounting import (
    Accounting,
    MeteringPolicy,
    Prices,
    UsageObservation,
    finalization_reserve,
    provenance,
)
from oss_contributor.codex import Codex
from oss_contributor.desktop import Desktop
from oss_contributor.errors import ProtocolFailure
from oss_contributor.models import Repair, ValidationReport


def observation(event="event-1", **changes):
    values = dict(event_id=event, thread_id="thread-1", input_tokens=100, cached_input_tokens=20,
                  output_tokens=10, reasoning_output_tokens=5, model="fixture-model", final=True)
    values.update(changes)
    return UsageObservation(**values)


def ledger(rig, policy=None):
    accounting = Accounting(rig[1])
    task = rig[1].create_task(rig[2].issue_value)
    accounting.start("call-1", task, provenance("fixture", "secret fixture input"), policy or MeteringPolicy())
    accounting.bind("call-1", "thread-1", "fixture-model")
    return accounting


def test_missing_usage_and_prices_are_not_zero(rig):
    accounting = ledger(rig)
    report = accounting.report()
    assert report["unmetered_calls"] == report["unpriced_calls"] == 1
    assert report["estimated_usd"] is None
    accounting.observe("call-1", observation())
    assert accounting.report()["observed_input_tokens"] == 100
    assert accounting.report()["estimated_usd"] is None


def test_usage_is_cumulative_deduplicated_and_old_snapshots_cannot_lower_it(rig):
    accounting = ledger(rig)
    accounting.observe("call-1", observation())
    accounting.observe("call-1", observation())
    accounting.observe("call-1", observation("older", input_tokens=50, cached_input_tokens=10,
                                                output_tokens=5, reasoning_output_tokens=2))
    assert accounting.report()["observed_input_tokens"] == 100
    assert rig[1].db.execute("SELECT COUNT(*) FROM model_usage_events").fetchone()[0] == 2
    with pytest.raises(ProtocolFailure, match="identity reused"):
        accounting.observe("call-1", observation("older", input_tokens=200))


def test_conflicting_or_foreign_usage_rejected_without_changing_charges(rig):
    accounting = ledger(rig)
    accounting.observe("call-1", observation())
    with pytest.raises(ProtocolFailure, match="another thread"):
        accounting.observe("call-1", observation("foreign", thread_id="another"))
    with pytest.raises(ProtocolFailure, match="inconsistent"):
        accounting.observe("call-1", observation("conflict", input_tokens=200, output_tokens=9))
    assert accounting.report()["observed_input_tokens"] == 100


def test_cost_uses_pinned_operator_prices_and_subsets_are_not_double_counted(rig):
    price = Prices(input_usd_per_million=2, cached_input_usd_per_million=1, output_usd_per_million=4)
    policy = MeteringPolicy(prices={"fixture-model": price})
    accounting = ledger(rig, policy)
    policy.prices["fixture-model"].input_usd_per_million = Decimal(999)
    accounting.observe("call-1", observation(input_tokens=1000000, cached_input_tokens=200000,
                                            output_tokens=100000, reasoning_output_tokens=50000))
    assert Decimal(accounting.report()["estimated_usd"]) == Decimal("2.2")
    accounting.observe("call-1", observation("new-model", model="other-model"))
    # An older snapshot is ignored, including its model metadata.
    assert accounting.report()["estimated_usd"] is not None
    accounting.observe("call-1", observation("new-model-live", input_tokens=1000001,
                                            cached_input_tokens=200000, output_tokens=100000,
                                            reasoning_output_tokens=50000, model="other-model"))
    assert accounting.report()["calls"][0]["data"]["trace"]["observed_model"] == "mixed"
    assert accounting.report()["estimated_usd"] is None


def test_new_partial_usage_does_not_remain_final(rig):
    accounting = ledger(rig)
    accounting.observe("call-1", observation())
    accounting.observe("call-1", observation("next-turn", input_tokens=101, final=False))
    assert accounting.get("call-1")["data"]["coverage"] == "partial"


@pytest.mark.parametrize("changes", [
    {"input_tokens": -1}, {"input_tokens": True}, {"cached_input_tokens": 101},
    {"reasoning_output_tokens": 11}, {"final": "true"},
])
def test_malformed_provider_counts_are_rejected(changes):
    with pytest.raises(ValidationError):
        observation(**changes)


def test_trace_hashes_exact_input_without_persisting_it(rig):
    accounting = ledger(rig)
    trace = accounting.get("call-1")["data"]["trace"]
    assert "secret fixture input" not in json.dumps(trace)
    assert trace["prompt_sha256"] != provenance("fixture", "changed input")["prompt_sha256"]
    assert trace["implementation_sha256"] and trace["model_identity_status"] == "reported_name"


def test_reserve_stays_inside_total_deadline_and_model_reservation(rig):
    desktop = Desktop(rig[4])
    action = desktop.tick()
    job = rig[1].desktop_job(action["ticket"])
    assert finalization_reserve(1800, 300) == 300
    assert finalization_reserve(3, 300) == 1
    assert job["expires"] - job["created"] == rig[0].limits.task_seconds
    assert action["worker_expires"] - job["created"] == 1500
    assert rig[1].usage()["model_seconds"] == 1500


def test_pinned_native_prompt_and_unknown_model_survive_resume(rig):
    desktop = Desktop(rig[4])
    action = desktop.tick()
    desktop.attach(action["ticket"], "thread-1")
    rig[0].limits.tools.max_steps = 999
    resumed = desktop.tick()
    assert resumed["prompt"] == action["prompt"]
    call = desktop.accounting_report()["calls"][0]
    assert call["data"]["trace"]["observed_model"] is None
    assert call["data"]["trace"]["prompt_sha256"]


def test_native_usage_limit_retains_slot_and_cannot_publish(rig, tmp_path):
    rig[0].metering.max_observed_tokens_per_call = 100
    desktop = Desktop(rig[4])
    action = desktop.tick()
    desktop.attach(action["ticket"], "thread-1")
    reserved = rig[1].usage()["model_seconds"]
    path = Path(action["job_path"]).parent / "usage-observation.json"
    path.write_text(observation().model_dump_json())
    assert desktop.record_usage(action["ticket"], path)["action"] == "cancel"
    rig[1].pause("pause")
    rig[1].set_meta("paused", "")
    assert desktop.tick()["action"] == "cancel"
    assert rig[1].usage()["model_seconds"] == reserved
    desktop.confirm_stopped(action["ticket"], "thread-1")
    assert not rig[2].published and not rig[1].desktop_jobs()


def test_enabled_limit_with_missing_telemetry_does_not_publish(rig, tmp_path):
    rig[0].metering.max_observed_tokens_per_call = 1000
    desktop = Desktop(rig[4])
    action = desktop.tick()
    result, _ = complete(desktop, action, tmp_path)
    assert "unavailable" in result["reason"]
    assert not rig[2].published


def test_finalization_time_is_available_after_worker_has_stopped(rig, monkeypatch):
    desktop = Desktop(rig[4])
    action = desktop.tick()
    desktop.attach(action["ticket"], "thread-1")
    monitored_read(desktop, action)
    desktop.confirm_stopped(action["ticket"], "thread-1")
    path = Path(action["handoff_path"])
    path.write_text(handoff(action).model_dump_json())
    job = rig[1].desktop_job(action["ticket"])
    stopped = job["data"]["stopped_at"]
    monkeypatch.setattr(time, "time", lambda: action["worker_expires"] + 1)
    deadlines = []

    def validate(*args):
        deadlines.append(args[-1] - time.monotonic())
        return ValidationReport(base_sha="a" * 40, failure="fixture stopped before publishing")

    monkeypatch.setattr(rig[4], "validate", validate)
    desktop.finish(action["ticket"], path)
    assert 298 <= deadlines[0] <= 300
    assert rig[1].usage()["model_seconds"] == pytest.approx(stopped - job["created"], abs=0.01)
    assert not rig[2].published


def test_late_stop_is_not_mistaken_for_reserved_verification_time(rig, monkeypatch):
    desktop = Desktop(rig[4])
    action = desktop.tick()
    desktop.attach(action["ticket"], "thread-1")
    monkeypatch.setattr(time, "time", lambda: action["worker_expires"] + 1)
    desktop.confirm_stopped(action["ticket"], "thread-1")
    assert rig[1].get(action["task_id"])["status"] == "failed"
    assert not rig[2].published


def test_standalone_token_usage_notifications_are_counted_once(rig, monkeypatch):
    accounting = ledger(rig)
    codex = Codex(rig[0])
    raw = {"inputTokens": 100, "cachedInputTokens": 20, "outputTokens": 10,
           "reasoningOutputTokens": 5, "totalTokens": 110}
    usage = {"method": "thread/tokenUsage/updated", "params": {
        "threadId": "thread-1", "turnId": "turn-1", "tokenUsage": {"total": raw, "last": raw},
    }}
    completed = {"method": "turn/completed", "params": {"turn": {"id": "turn-1", "status": "completed", "items": [
        {"type": "agentMessage", "text": '{"summary":"fixture","root_cause":"fixture","evidence":["fixture"]}'},
    ]}}}

    @contextmanager
    def session(deadline, handler):
        client, _ = rpc([{"id": 1, "result": {"turn": {"id": "turn-1"}}}, usage, usage, completed], handler)
        yield client, "/fixture"

    monkeypatch.setattr(codex, "_thread", lambda *args: "thread-1")
    monkeypatch.setattr(codex, "_session", session)
    with FixtureSandbox(None, BASE, time.monotonic() + 10) as box, codex.metered(accounting, "call-1"):
        codex.ask(Repair, "fixture", box, time.monotonic() + 10)
    assert accounting.report()["observed_input_tokens"] == 100
    assert accounting.get("call-1")["data"]["coverage"] == "reported"
