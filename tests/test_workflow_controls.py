from __future__ import annotations

import time

from conftest import FixtureSandbox
from test_engine import run_issue

from oss_contributor.desktop import Desktop
from oss_contributor.store import Store


def test_legacy_evidence_is_revalidated_before_recovery(rig):
    task = run_issue(rig)
    report = task["data"]["report"]
    report.pop("evidence_digest")
    rig[1].update(task["id"], "publishing", report=report)
    before = FixtureSandbox.created
    rig[4].recover()
    assert FixtureSandbox.created == before + 1
    assert rig[1].get(task["id"])["data"]["report"]["evidence_digest"]
    assert len(rig[2].published) == 1


def test_changed_commands_invalidate_previous_pass(rig):
    task = run_issue(rig)
    rig[1].update(task["id"], "validated")
    rig[0].repositories[0].checks = [["python", "-c", "raise SystemExit(1)"]]
    rig[4].recover()
    assert rig[1].get(task["id"])["status"] == "needs_attention"
    assert not rig[1].get(task["id"])["data"]["last_attempt_report"]["passed"]
    assert len(rig[2].published) == 1


def test_changed_patch_cannot_reuse_a_passed_report(rig):
    task = run_issue(rig)
    changes = task["data"]["changes"]
    changes["calc.py"] = "def add(a, b):\n    return 0\n"
    rig[1].update(task["id"], "validated", changes=changes)
    rig[4].recover()
    assert rig[1].get(task["id"])["status"] == "needs_attention"
    assert len(rig[2].published) == 1


def test_pause_and_immediate_resume_keep_native_cancellation_durable(rig):
    desktop = Desktop(rig[4])
    action = desktop.tick()
    desktop.attach(action["ticket"], "native-running")
    reserved = rig[1].usage()["model_seconds"]
    rig[1].pause("operator stop")
    rig[1].set_meta("paused", "")
    second = Store(rig[0])
    try:
        assert second.desktop_job(action["ticket"])["state"] == "cancelling"
    finally:
        second.close()
    assert desktop.tick()["action"] == "cancel"
    assert rig[1].usage()["model_seconds"] == reserved
    desktop.confirm_stopped(action["ticket"], "native-running")
    assert not rig[1].desktop_jobs()


def test_stop_during_publication_preflight_is_not_overwritten(rig):
    task = run_issue(rig)
    rig[1].update(task["id"], "validated")
    original = rig[2].base

    def stop_before_write(repo):
        rig[1].update(task["id"], "stopped")
        return original(repo)

    rig[2].base = stop_before_write
    rig[4].recover()
    assert rig[1].get(task["id"])["status"] == "stopped"
    assert len(rig[2].published) == 1


def test_publication_recovery_is_bounded_across_restart(rig):
    task = run_issue(rig)
    rig[0].limits.publication_recovery_attempts = 2
    rig[1].update(task["id"], "publishing")

    def unavailable(*args, **kwargs):
        raise OSError("temporary network failure")

    rig[2].publish = unavailable
    for attempt in range(2):
        rig[1].update(task["id"], next_reconcile=0)
        rig[4].recover()
        current = rig[1].get(task["id"])
        assert current["data"]["reconcile_attempts"] == attempt + 1
        assert current["data"]["next_reconcile"] >= time.time() + 590 * 2**attempt
    second = Store(rig[0])
    try:
        assert second.get(task["id"])["data"]["reconcile_attempts"] == 2
    finally:
        second.close()
    rig[1].update(task["id"], next_reconcile=0)
    rig[4].recover()
    assert rig[1].get(task["id"])["status"] == "needs_attention"
    assert len(rig[2].published) == 1


def test_recovery_wait_does_not_reset_time_window(rig):
    task = run_issue(rig)
    rig[1].update(task["id"], "validated", reconcile_started=time.time() - 86401)
    rig[4].recover()
    assert rig[1].get(task["id"])["status"] == "needs_attention"
    assert len(rig[2].published) == 1


def test_stop_during_inflight_write_records_result_without_reopening(rig):
    task = run_issue(rig)
    rig[1].update(task["id"], "validated")
    original = rig[2].publish

    def stop_during_write(*args, **kwargs):
        result = original(*args, **kwargs)
        rig[1].update(task["id"], "stopped")
        return result

    rig[2].publish = stop_during_write
    rig[4].recover()
    current = rig[1].get(task["id"])
    assert current["status"] == "stopped"
    assert current["data"]["pr"]["number"] == 8
    assert len(rig[2].published) == 1
