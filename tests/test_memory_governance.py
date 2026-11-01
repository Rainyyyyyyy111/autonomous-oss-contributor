from __future__ import annotations

import json
import time

import pytest
from typer.testing import CliRunner

import oss_contributor.learning as learning_module
from oss_contributor.cli import app
from oss_contributor.desktop import Desktop
from oss_contributor.errors import PermissionDenied
from oss_contributor.learning import LearningMemory
from oss_contributor.models import Issue, ReviewEvent
from oss_contributor.store import Store


def source(rig, body="Please shorten the PR description. private-fixture-text", event_id="review:node:2026-10-01T00:00:00Z"):
    ident = rig[1].create_task(rig[2].issue_value)
    rig[1].update(ident, "open", pr={"number": 8, "url": rig[2].state.url, "head_sha": "b" * 40})
    rig[2].state.events = [ReviewEvent(id=event_id, body=body)]
    memory = LearningMemory(rig[1])
    memory.observe(rig[1].get(ident), rig[2].state)
    return memory, ident, memory.records()[0]["record_id"]


def test_ttl_is_pinned_and_polling_does_not_extend_it(rig, monkeypatch):
    memory, ident, _ = source(rig)
    record = memory.records()[0]
    rig[0].memory.ttl_days = 3650
    monkeypatch.setattr(time, "time", lambda: record["expires_at"] + 1)
    memory.observe(rig[1].get(ident), rig[2].state)
    assert memory.records()[0]["expires_at"] == record["expires_at"]
    assert memory.records()[0]["status"] == "expired"
    assert not memory.context("owner/demo")["checks"]


def test_updated_feedback_supersedes_old_version_and_old_poll_does_not_revive_it(rig):
    memory, ident, old = source(rig)
    old_event = rig[2].state.events[0].model_copy(deep=True)
    rig[2].state.events = [ReviewEvent(id="review:node:2026-10-02T00:00:00Z", body="Thanks!")]
    memory.observe(rig[1].get(ident), rig[2].state)
    assert next(row for row in memory.records() if row["record_id"] == old)["status"] == "superseded"
    assert not memory.context("owner/demo")["checks"]
    rig[2].state.events = [old_event]
    memory.observe(rig[1].get(ident), rig[2].state)
    assert not memory.context("owner/demo")["checks"]


def test_body_edit_with_unchanged_event_id_has_a_stable_local_revision(rig):
    memory, ident, old = source(rig)
    rig[2].state.events[0].body = "Thanks!"
    memory.observe(rig[1].get(ident), rig[2].state)
    assert len(memory.records()) == 2
    assert next(row for row in memory.records() if row["record_id"] == old)["status"] == "superseded"
    assert not memory.context("owner/demo")["checks"]


def test_invalidation_is_audited_and_reobservation_cannot_reactivate_it(rig):
    memory, ident, record_id = source(rig)
    assert memory.mutate(ident, record_id, delete=False)["invalidated"]
    memory.observe(rig[1].get(ident), rig[2].state)
    assert memory.records()[0]["status"] == "invalidated"
    assert not memory.context("owner/demo")["checks"]
    assert memory.mutate(ident, record_id, delete=False)["already_invalidated"]
    assert len(memory.audit()) == 1 and memory.audit()[0]["action"] == "invalidate"


def test_deletion_removes_learning_payload_and_exports_without_changing_task_state(rig):
    memory, ident, record_id = source(rig)
    task_before = rig[1].get(ident)
    rig[1].mark_seen(ident, [rig[2].state.events[0].id])
    assert "private-fixture-text" in (rig[1].home / "learning/feedback.json").read_text()
    assert memory.mutate(ident, record_id, delete=True)["deleted"]
    assert not memory.records()
    assert "private-fixture-text" not in (rig[1].home / "learning/feedback.json").read_text()
    assert "private-fixture-text" not in json.dumps(memory.audit())
    assert rig[1].get(ident) == task_before
    assert rig[1].seen(ident, rig[2].state.events[0].id)
    assert memory.mutate(ident, record_id, delete=True)["already_deleted"]
    assert len(memory.audit()) == 1
    memory.observe(rig[1].get(ident), rig[2].state)
    assert not memory.records()  # Tombstone survives routine refresh.
    reopened = Store(rig[0])
    try:
        assert not LearningMemory(reopened).records()
        assert len(LearningMemory(reopened).audit()) == 1
    finally:
        reopened.close()


def test_deleting_latest_version_does_not_reactivate_older_guidance(rig):
    memory, ident, _ = source(rig)
    rig[2].state.events = [ReviewEvent(id="review:node:2026-10-02T00:00:00Z", body="Please revise the PR description.")]
    memory.observe(rig[1].get(ident), rig[2].state)
    latest = next(row for row in memory.records() if row["status"] == "active")
    memory.mutate(ident, latest["record_id"], delete=True)
    assert not memory.context("owner/demo")["checks"]
    assert memory.records()[0]["status"] == "superseded"


def test_export_failure_after_delete_cannot_leave_stale_payload(rig, monkeypatch):
    memory, ident, record_id = source(rig)

    def disk_full(*args):
        raise OSError("synthetic disk full")

    monkeypatch.setattr(learning_module, "atomic_write", disk_full)
    result = memory.mutate(ident, record_id, delete=True)
    assert result["deleted"] and not result["exports_rebuilt"]
    assert not memory.records()
    assert not (rig[1].home / "learning/feedback.json").exists()
    assert not (rig[1].home / "learning/report.md").exists()


def test_linked_export_directory_cannot_delete_external_files(rig, tmp_path):
    memory, ident, record_id = source(rig)
    directory = rig[1].home / "learning"
    for child in directory.iterdir():
        child.unlink()
    directory.rmdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    protected = outside / "feedback.json"
    protected.write_text("protected fixture")
    directory.symlink_to(outside, target_is_directory=True)
    with pytest.raises(PermissionDenied, match="linked"):
        memory.mutate(ident, record_id, delete=True)
    assert protected.read_text() == "protected fixture"
    assert memory.records() and not memory.audit()


def test_scope_filtering_prevents_foreign_reports_and_mutations(rig):
    memory, ident, record_id = source(rig)
    other = rig[1].create_task(Issue(repo="owner/demo", number=2, title="other"))
    rig[1].update(other, pr={"number": 9})
    Desktop(rig[4]).bind_scope(True, [other])
    assert not memory.records() and not memory.outcomes()
    with pytest.raises(PermissionDenied):
        memory.mutate(ident, record_id, delete=True)
    assert not memory.audit()


def test_native_pinned_memory_reference_blocks_deletion_until_job_settles(rig):
    memory, ident, record_id = source(rig)
    rig[1].update(ident, "closed")
    rig[2].issue_value.number = 2
    rig[1].create_task(rig[2].issue_value)
    action = Desktop(rig[4]).tick()
    assert action["action"] == "dispatch"
    with pytest.raises(PermissionDenied, match="referencing"):
        memory.mutate(ident, record_id, delete=True)
    assert memory.records() and not memory.audit()


def test_context_is_bounded_and_report_retains_inactive_evidence(rig):
    memory, ident, _ = source(rig)
    rig[0].memory.max_context_records = 1
    rig[2].state.events = [
        ReviewEvent(id="separate-review", body="Please fix the implementation.", path="calc.py"),
    ]
    memory.observe(rig[1].get(ident), rig[2].state)
    context = memory.context("owner/demo")
    assert sum(check["observations"] for check in context["checks"]) == 1
    assert len(memory.report()["records"]) == 2


def test_memory_delete_cli_is_local_and_audit_has_no_source_text(rig, monkeypatch):
    from oss_contributor import cli

    _, ident, record_id = source(rig)
    monkeypatch.setattr(cli, "load", lambda path: rig[0])
    runner = CliRunner()
    result = runner.invoke(app, ["learning", "delete", str(ident), record_id])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["deleted"]
    audit = runner.invoke(app, ["learning", "audit"])
    assert audit.exit_code == 0 and "private-fixture-text" not in audit.output
    assert not rig[2].published and not rig[3].calls


def test_legacy_expiry_is_pinned_once_and_not_extended_by_later_configuration(rig):
    memory, ident, record_id = source(rig)
    row = rig[1].db.execute("SELECT data FROM feedback_memory WHERE task=? AND event=?", (ident, record_id)).fetchone()
    data = json.loads(row[0])
    del data["expires_at"]
    with rig[1].db:
        rig[1].db.execute("UPDATE feedback_memory SET data=? WHERE task=? AND event=?", (json.dumps(data), ident, record_id))
    migrated = Store(rig[0])
    try:
        expiry = LearningMemory(migrated).records()[0]["expires_at"]
    finally:
        migrated.close()
    rig[0].memory.ttl_days = 3650
    reopened = Store(rig[0])
    try:
        assert LearningMemory(reopened).records()[0]["expires_at"] == expiry
    finally:
        reopened.close()
