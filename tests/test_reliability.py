from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest
from test_desktop import complete, handoff
from typer.testing import CliRunner

from oss_contributor import installation
from oss_contributor.cli import app
from oss_contributor.desktop import Desktop
from oss_contributor.models import Issue
from oss_contributor.process import ContributorError
from oss_contributor.store import Store


def test_monitor_scope_blocks_discovery_and_unrelated_publication_recovery(rig, tmp_path):
    desktop = Desktop(rig[4])
    first = desktop.tick()
    complete(desktop, first, tmp_path)
    other = rig[1].create_task(
        Issue(repo="outside/other", number=2, title="not authorized by this monitor")
    )
    rig[1].update(other, "validated", report={"passed": True})
    desktop.bind_scope(True, [first["task_id"]])
    rig[0].limits.prs_per_day = 5

    def forbidden(*args):
        raise AssertionError("monitor must never discover")

    rig[2].discover = forbidden
    desktop.tick()  # Even forgetting --monitor-only cannot expand this persisted scope.
    assert rig[1].get(other)["status"] == "validated"
    assert len(rig[2].published) == 1
    rig[4].once()  # The optional standalone worker cannot expand a native monitor either.
    assert not rig[3].calls
    with pytest.raises(ContributorError, match="scope"):
        rig[4].publish(rig[1].get(other), time.monotonic() + 30)
    foreign = rig[1].create_task(Issue(repo="outside/open", number=3, title="other PR"))
    rig[1].update(foreign, "open", pr={"number": 999, "head_sha": "a" * 40})
    original = rig[2].pr_state

    def bounded_observation(repo, number):
        assert repo == "owner/demo" and number == 8
        return original(repo, number)

    rig[2].pr_state = bounded_observation
    rig[4].watch()
    with pytest.raises(ContributorError, match="scope"):
        desktop.dispatch(rig[1].get(other))
    second = Store(rig[0])
    try:
        assert json.loads(second.meta("desktop_scope"))["targets"][0]["task"] == first["task_id"]
    finally:
        second.close()


def test_monitor_only_shows_and_acknowledges_its_own_notices(rig, tmp_path):
    desktop = Desktop(rig[4])
    first = desktop.tick()
    complete(desktop, first, tmp_path)
    desktop.bind_scope(True, [first["task_id"]])
    rig[1].notify("foreign", task=999, kind="needs_attention")
    visible = desktop.result()["notifications"]
    assert all(n["task"] == first["task_id"] for n in visible)
    foreign = next(n["id"] for n in rig[1].notifications() if n["task"] == 999)
    with pytest.raises(ContributorError, match="scope"):
        desktop.acknowledge([foreign])
    desktop.acknowledge([n["id"] for n in visible])
    desktop.acknowledge([n["id"] for n in visible])  # Retrying ack is idempotent.
    assert not desktop.result()["notifications"]
    assert rig[1].notifications()[0]["task"] == 999


def test_monitor_completion_ignores_unrelated_open_tasks(rig, tmp_path):
    desktop = Desktop(rig[4])
    first = desktop.tick()
    complete(desktop, first, tmp_path)
    desktop.bind_scope(True, [first["task_id"]])
    other = rig[1].create_task(Issue(repo="outside/other", number=2, title="other"))
    rig[1].update(other, "open", pr={"number": 3})
    assert not desktop.result()["scope_complete"]
    rig[1].update(first["task_id"], "merged")
    assert desktop.result()["scope_complete"]


def test_monitor_identity_change_does_not_follow_a_different_pr(rig, tmp_path):
    desktop = Desktop(rig[4])
    first = desktop.tick()
    complete(desktop, first, tmp_path)
    desktop.bind_scope(True, [first["task_id"]])
    rig[1].update(first["task_id"], pr={"number": 999, "head_sha": "a" * 40})
    rig[1].set_meta("desktop_last_watch", "0")
    result = desktop.tick()
    assert "blocked" in result["reason"]
    assert "scope" in result["notifications"][-1]["reason"]
    assert len(rig[2].published) == 1


def test_scope_cannot_change_during_an_active_chat(rig):
    desktop = Desktop(rig[4])
    desktop.tick()
    with pytest.raises(ContributorError, match="active"):
        desktop.bind_scope(False)


def test_fixed_monitor_rejects_foreign_chat_tickets_without_settling_them(rig, tmp_path):
    desktop = Desktop(rig[4])
    first = desktop.tick()
    complete(desktop, first, tmp_path)
    desktop.bind_scope(True, [first["task_id"]])
    other = rig[1].create_task(Issue(repo="outside/other", number=2, title="other"))
    job = rig[1].desktop_job(first["ticket"])
    job.update(ticket="foreign-ticket", task=other, state="attached", expires=time.time() + 300)
    rig[1].save_desktop_job(job)
    with pytest.raises(ContributorError, match="scope"):
        desktop.fail("foreign-ticket", "must not touch")
    assert desktop.tick()["action"] == "wait"
    assert rig[1].desktop_job("foreign-ticket")["state"] == "attached"


def test_native_handoff_requires_explicit_terminal_confirmation(rig, tmp_path):
    desktop = Desktop(rig[4])
    action = desktop.tick()
    desktop.attach(action["ticket"], "native-1")
    path = Path(action["handoff_path"])
    path.write_text(handoff(action).model_dump_json())
    with pytest.raises(ContributorError, match="termination"):
        desktop.finish(action["ticket"], path)
    assert not rig[2].published
    with pytest.raises(ContributorError, match="assigned"):
        desktop.confirm_stopped(action["ticket"], "wrong-chat")
    desktop.confirm_stopped(action["ticket"], "native-1")
    desktop.finish(action["ticket"], path)
    assert len(rig[2].published) == 1


def test_fail_does_not_free_budget_or_slot_for_a_still_running_chat(rig):
    desktop = Desktop(rig[4])
    action = desktop.tick()
    desktop.attach(action["ticket"], "native-1")
    reserved = rig[1].usage()["model_seconds"]
    assert desktop.fail(action["ticket"], "tool failed")["action"] == "cancel"
    assert rig[1].usage()["model_seconds"] == reserved
    rig[1].set_meta("paused", "operator paused")
    assert desktop.tick()["action"] == "cancel"  # Stop requests survive pause and resume.
    rig[1].set_meta("paused", "")
    assert desktop.tick()["action"] == "cancel"
    rig[4].once()
    assert not rig[3].calls
    desktop.confirm_stopped(action["ticket"], "native-1")
    assert not rig[1].desktop_jobs()
    assert rig[1].usage()["model_seconds"] < reserved


def test_ambiguous_pending_creation_requires_explicit_absence_confirmation(rig):
    desktop = Desktop(rig[4])
    action = desktop.tick()
    desktop.fail(action["ticket"], "creation result lost")
    with pytest.raises(ContributorError, match="reconcile"):
        desktop.confirm_stopped(action["ticket"])
    desktop.confirm_stopped(action["ticket"], not_created=True)
    desktop.confirm_stopped(action["ticket"], not_created=True)
    assert not rig[1].desktop_jobs()
    assert not rig[2].published


def test_legacy_failed_lease_is_reconciled_without_refunding_twice(rig):
    desktop = Desktop(rig[4])
    action = desktop.tick()
    desktop.attach(action["ticket"], "legacy-chat")
    job = rig[1].desktop_job(action["ticket"])
    desktop.settle(job, "failed")  # 0.2 had no persisted native termination acknowledgement.
    rig[1].update(action["task_id"], "failed")
    budget = rig[1].usage()["model_seconds"]
    result = desktop.tick()
    assert result["action"] == "cancel"
    assert result["thread_id"] == "legacy-chat"
    rig[4].once()
    assert not rig[3].calls
    desktop.confirm_stopped(action["ticket"], "legacy-chat")
    assert rig[1].usage()["model_seconds"] == budget
    assert not rig[1].desktop_jobs()


def test_lost_creation_can_be_bound_for_cancellation_after_expiry_and_pause(rig):
    desktop = Desktop(rig[4])
    action = desktop.tick()
    desktop.fail(action["ticket"], "lost creation response")
    job = rig[1].desktop_job(action["ticket"])
    job["expires"] = time.time() - 1
    rig[1].save_desktop_job(job)
    rig[1].set_meta("paused", "operator paused")
    result = desktop.attach(action["ticket"], "reconciled-existing-chat")
    assert result["action"] == "cancel"
    desktop.confirm_stopped(action["ticket"], "reconciled-existing-chat")
    assert not rig[1].desktop_jobs()
    assert rig[1].meta("paused") == "operator paused"


def test_health_is_read_only_and_explains_budget_and_timezone(rig):
    desktop = Desktop(rig[4])

    def forbidden(*args, **kwargs):
        raise AssertionError("health must not access a remote service")

    rig[2].discover = rig[2].pr_state = forbidden
    rig[1].reserve("publications")
    state = desktop.status()
    assert state["reason"] == "daily publication budget exhausted"
    assert state["budget_remaining"]["publications"] == 0
    assert state["budget_resets_at"].endswith("+08:00")
    assert not rig[1].tasks()
    assert not rig[3].calls


def test_managed_skill_upgrade_preserves_config_and_extra_files(tmp_path):
    config = tmp_path / "operator.toml"
    target = installation.install_skill(tmp_path / "skills", config)
    (target / "notes.txt").write_text("operator notes")
    installation.install_skill(tmp_path / "skills", upgrade=True)
    assert json.loads((target / "installation.json").read_text())["config"] == str(config)
    assert (target / "notes.txt").read_text() == "operator notes"
    assert (target / ".install-manifest.json").is_file()


def test_managed_skill_upgrade_refuses_local_edits_before_writing(tmp_path):
    target = installation.install_skill(tmp_path / "skills", tmp_path / "config.toml")
    (target / "SKILL.md").write_text("operator instructions")
    before = {str(p): p.read_bytes() for p in target.rglob("*") if p.is_file()}
    with pytest.raises(ContributorError, match="locally modified"):
        installation.install_skill(tmp_path / "skills", upgrade=True)
    assert before == {str(p): p.read_bytes() for p in target.rglob("*") if p.is_file()}


def test_managed_skill_upgrade_rejects_linked_directories(tmp_path):
    target = installation.install_skill(tmp_path / "skills", tmp_path / "config.toml")
    external = tmp_path / "external"
    external.mkdir()
    (external / "openai.yaml").write_text("external data")
    shutil.rmtree(target / "agents")
    (target / "agents").symlink_to(external, target_is_directory=True)
    with pytest.raises(ContributorError, match="linked"):
        installation.install_skill(tmp_path / "skills", upgrade=True)
    assert (external / "openai.yaml").read_text() == "external data"


def test_interrupted_skill_upgrade_can_resume_without_overwriting_customizations(
    tmp_path, monkeypatch
):
    vendor = tmp_path / "vendor"
    shutil.copytree(installation.SOURCE, vendor)
    monkeypatch.setattr(installation, "SOURCE", vendor)
    target = installation.install_skill(tmp_path / "skills", tmp_path / "config.toml")
    (vendor / "SKILL.md").write_text((vendor / "SKILL.md").read_text() + "\nVendor update.\n")
    write = installation.atomic_write

    def interrupted(path, data):
        if path.name == ".install-manifest.json":
            raise OSError("simulated interruption")
        write(path, data)

    monkeypatch.setattr(installation, "atomic_write", interrupted)
    with pytest.raises(OSError, match="interruption"):
        installation.install_skill(tmp_path / "skills", upgrade=True)
    monkeypatch.setattr(installation, "atomic_write", write)
    installation.install_skill(tmp_path / "skills", upgrade=True)
    assert (target / "SKILL.md").read_bytes() == (vendor / "SKILL.md").read_bytes()


def test_cli_scope_and_health_expose_persisted_monitor_mode(rig, tmp_path):
    desktop = Desktop(rig[4])
    first = desktop.tick()
    complete(desktop, first, tmp_path)
    config = tmp_path / "config.toml"
    config.write_text("state_dir = " + json.dumps(str(rig[0].state_dir)) + "\n")
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["desktop", "scope", "--monitor", "--task-id", str(first["task_id"]), "-c", str(config)],
    )
    assert result.exit_code == 0, result.output
    status = runner.invoke(app, ["desktop", "status", "-c", str(config)])
    assert status.exit_code == 0, status.output
    assert json.loads(status.stdout)["scope"]["mode"] == "monitor"
    setup = runner.invoke(app, ["desktop", "setup", "-c", str(config)])
    assert setup.exit_code == 0, setup.output
    assert "monitor-only" in json.loads(setup.stdout)["prompt"]
