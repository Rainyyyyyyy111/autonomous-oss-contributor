from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from conftest import BASE, FIX, REGRESSION
from typer.testing import CliRunner

from oss_contributor.cli import app
from oss_contributor.desktop import Desktop, Handoff, install_skill
from oss_contributor.models import Assessment, Repair, ReviewEvent
from oss_contributor.process import ContributorError, RateLimited
from oss_contributor.store import Store
from oss_contributor.worker_tools import WorkerTools


def handoff(action, **updates):
    values = dict(
        ticket=action["ticket"],
        outcome="fixed",
        assessment=Assessment(
            suitable=True,
            reason="bounded arithmetic bug",
            evidence=["positive operands subtract"],
            reproduction="add(2, 3)",
            expected_scope=["calc.py"],
            validation="full pytest suite",
        ),
        repair=Repair(
            summary="Use addition", root_cause="wrong operator", evidence=["real red/green test"]
        ),
        changes={"calc.py": FIX, "tests/test_oss_regression_1.py": REGRESSION},
    )
    values.update(updates)
    return Handoff(**values)


def complete(desktop, action, tmp_path, **updates):
    thread_id = (
        desktop.store.desktop_job(action["ticket"])["data"].get("thread_id")
        or "native-" + action["ticket"]
    )
    desktop.attach(action["ticket"], thread_id)
    if (updates.get("outcome", "fixed") == "fixed"
        and not desktop.store.desktop_job(action["ticket"])["data"].get("thread_stopped")):
        monitored_read(desktop, action)
    desktop.confirm_stopped(action["ticket"], thread_id)
    path = Path(action["handoff_path"])
    path.write_text(handoff(action, **updates).model_dump_json())
    return desktop.finish(action["ticket"], path), path


def monitored_read(desktop, action):
    from conftest import FixtureSandbox

    with WorkerTools(action["job_path"], github=desktop.engine.github,
                     sandbox_factory=FixtureSandbox) as worker:
        worker.tool("repo_read", {"path": "calc.py"})


def test_automatic_discovery_dispatch_resume_and_verified_publication(rig, tmp_path):
    desktop = Desktop(rig[4])
    action = desktop.tick()
    assert action["action"] == "dispatch"
    assert rig[1].usage()["attempts"] == 1
    assert not rig[3].calls  # No CLI/App Server invocation anywhere in this path.
    assert desktop.tick()["ticket"] == action["ticket"]
    desktop.attach(action["ticket"], "native-chat-1")
    desktop.checkpoint(action["ticket"], "cursor:3")
    second = Store(rig[0])
    try:
        from conftest import FixtureSandbox

        from oss_contributor.engine import Engine

        recovered = Desktop(Engine(rig[0], second, rig[2], rig[3], FixtureSandbox)).tick()
        assert recovered["action"] == "resume"
        assert recovered["thread_id"] == "native-chat-1"
        assert recovered["after_cursor"] == "cursor:3"
    finally:
        second.close()
    result, path = complete(desktop, action, tmp_path)
    task = rig[1].get(action["task_id"])
    assert task["status"] == "open"
    assert [cmd["code"] for cmd in task["data"]["report"]["commands"]] == [0, 1, 0, 0]
    assert result["notifications"][0]["kind"] == "pr_created"
    assert not rig[1].desktop_jobs()
    assert rig[1].usage()["model_seconds"] < rig[0].limits.task_seconds
    desktop.finish(action["ticket"], path)
    assert len(rig[2].published) == 1
    assert len(rig[1].notifications()) == 1
    rig[1].acknowledge([result["notifications"][0]["id"]])
    assert not desktop.tick()["notifications"]


def test_empty_allowlist_pause_and_monitor_only_do_not_select_issues(rig):
    desktop = Desktop(rig[4])
    assert desktop.tick(monitor_only=True)["action"] == "wait"
    assert not rig[1].tasks()
    rig[0].repositories.clear()
    assert desktop.tick()["action"] == "wait"
    rig[1].set_meta("paused", "operator paused")
    assert desktop.tick()["reason"] == "operator paused"
    assert not rig[1].tasks()


def test_standalone_worker_cannot_compete_with_an_active_native_job(rig):
    action = Desktop(rig[4]).tick()
    assert action["action"] == "dispatch"
    rig[4].once()
    assert not rig[3].calls
    assert len(rig[1].desktop_jobs()) == 1
    assert rig[1].get(action["task_id"])["status"] == "desktop_pending"


@pytest.mark.parametrize("kind", ["attempts", "publications", "model_seconds"])
def test_exhausted_budget_never_dispatches_or_invokes_cli_model(rig, kind):
    desktop = Desktop(rig[4])
    amount = {"attempts": 3, "publications": 1, "model_seconds": 7200}[kind]
    assert rig[1].reserve(kind, amount)
    assert desktop.tick()["action"] == "wait"
    assert not rig[1].desktop_jobs()
    assert not rig[3].calls


def test_conflicting_issue_is_skipped_before_native_budget(rig):
    rig[2].issue_value.conflicts = ["someone has an open fix"]
    assert Desktop(rig[4]).tick()["action"] == "wait"
    assert rig[1].usage()["attempts"] == 0
    assert rig[1].usage()["model_seconds"] == 0


def test_expired_chat_keeps_slot_until_termination_is_confirmed(rig):
    desktop = Desktop(rig[4])
    action = desktop.tick()
    desktop.attach(action["ticket"], "native-chat-1")
    job = rig[1].desktop_job(action["ticket"])
    job["created"] = time.time() - job["data"]["reserved_seconds"] - 1
    job["expires"] = time.time() - 1
    rig[1].save_desktop_job(job)
    result = desktop.tick()
    assert result["action"] == "cancel"
    assert rig[1].desktop_jobs()[0]["state"] == "cancelling"
    rig[1].set_meta("paused", "")  # Resume cannot release an unconfirmed native chat.
    assert desktop.tick()["action"] == "cancel"
    assert rig[1].usage()["attempts"] == 1
    assert rig[1].usage()["model_seconds"] == job["data"]["reserved_seconds"]
    desktop.confirm_stopped(action["ticket"], "native-chat-1")
    assert not rig[1].desktop_jobs()
    assert len(rig[1].notifications()) == 1


def test_job_cannot_be_attached_to_two_chats(rig):
    desktop = Desktop(rig[4])
    action = desktop.tick()
    desktop.attach(action["ticket"], "native-chat-1")
    with pytest.raises(ContributorError, match="another chat"):
        desktop.attach(action["ticket"], "native-chat-2")


@pytest.mark.parametrize("mode", ["wrong", "weaken", "baseline"])
def test_worker_claim_cannot_bypass_independent_quality_gate(rig, tmp_path, mode):
    desktop = Desktop(rig[4])
    action = desktop.tick()
    changes = handoff(action).changes
    if mode == "wrong":
        changes["calc.py"] = BASE["calc.py"] + "\n# claimed fixed\n"
    if mode == "weaken":
        changes["tests/test_existing.py"] = "def test_nothing(): pass\n"
    if mode == "baseline":
        rig[2].files["tests/test_existing.py"] = "def test_broken(): assert False\n"
    complete(desktop, action, tmp_path, changes=changes)
    assert not rig[2].published
    assert rig[1].get(action["task_id"])["status"] == "failed"


def test_handoff_identity_and_schema_reject_worker_authorization(rig, tmp_path):
    desktop = Desktop(rig[4])
    action = desktop.tick()
    desktop.attach(action["ticket"], "native-chat-1")
    desktop.confirm_stopped(action["ticket"], "native-chat-1")
    path = Path(action["handoff_path"])
    payload = handoff(action).model_dump()
    payload["ticket"] = "different-job"
    path.write_text(json.dumps(payload))
    with pytest.raises(ContributorError, match="another job"):
        desktop.finish(action["ticket"], path)
    payload["ticket"] = action["ticket"]
    payload["repository"] = {"name": "outside/unapproved"}
    path.write_text(json.dumps(payload))
    with pytest.raises(ContributorError, match="schema"):
        desktop.finish(action["ticket"], path)
    assert not rig[2].published


def test_authorization_change_during_native_repair_blocks_publish(rig, tmp_path):
    desktop = Desktop(rig[4])
    action = desktop.tick()
    rig[0].repositories[0].checks = [["python", "-m", "ruff", "check", "."]]
    complete(desktop, action, tmp_path)
    assert not rig[2].published
    assert "authorization" in rig[1].get(action["task_id"])["data"]["reason"]


def test_quota_handoff_pauses_and_settlement_is_idempotent(rig, tmp_path):
    desktop = Desktop(rig[4])
    action = desktop.tick()
    complete(desktop, action, tmp_path, outcome="quota", reason="native account usage limit")
    usage = rig[1].usage()
    desktop.fail(action["ticket"], "same failure", quota=True)
    assert rig[1].usage() == usage
    assert rig[1].meta("paused")
    assert not rig[2].published


def test_verified_publication_interruption_recovers_once_and_notifies(rig, tmp_path):
    desktop = Desktop(rig[4])
    action = desktop.tick()
    original = rig[2].publish

    def broken(*args, **kwargs):
        original(*args, **kwargs)
        raise ContributorError("connection lost after remote PR creation")

    rig[2].publish = broken
    complete(desktop, action, tmp_path)
    assert rig[1].get(action["task_id"])["status"] == "publishing"
    rig[2].publish = original
    rig[1].update(action["task_id"], next_reconcile=0)
    desktop.tick()
    assert rig[1].get(action["task_id"])["status"] == "open"
    assert len(rig[2].published) == 1
    assert len(rig[1].notifications()) == 1


def test_pending_ci_and_acknowledgement_do_not_create_revision_chat(rig, tmp_path):
    desktop = Desktop(rig[4])
    action = desktop.tick()
    complete(desktop, action, tmp_path)
    rig[2].state.waiting = "CI awaiting maintainer authorization"
    rig[2].state.events = [ReviewEvent(id="ci:1", kind="ci", body="failed log")]
    rig[1].set_meta("desktop_last_watch", "0")
    assert desktop.tick()["action"] == "wait"
    rig[2].state.waiting = ""
    rig[2].state.events = [ReviewEvent(id="review:thanks", body="Thanks!")]
    rig[1].set_meta("desktop_last_watch", "0")
    assert desktop.tick()["action"] == "wait"
    assert rig[1].seen(action["task_id"], "review:thanks")


def test_feedback_revision_uses_new_ticket_and_preserves_previous_head(rig, tmp_path):
    desktop = Desktop(rig[4])
    first = desktop.tick()
    complete(desktop, first, tmp_path)
    previous = rig[1].get(first["task_id"])["data"]["pr"]["head_sha"]
    rig[2].state.events = [ReviewEvent(id="review:doc", body="Please document the function.")]
    rig[1].set_meta("desktop_last_watch", "0")
    revision = desktop.tick()
    assert revision["action"] == "dispatch"
    assert revision["ticket"] != first["ticket"]
    assert rig[1].usage()["attempts"] == 1
    assert rig[1].desktop_job(revision["ticket"])["data"]["previous_head"] == previous
    original = rig[2].publish
    heads = []

    def publish(*args, **kwargs):
        heads.append(kwargs.get("previous_head"))
        return original(*args, **kwargs)

    rig[2].publish = publish
    complete(
        desktop,
        revision,
        tmp_path,
        changes={
            "calc.py": 'def add(a, b):\n    """Return the sum."""\n    return a + b\n',
            "tests/test_oss_regression_1.py": REGRESSION,
        },
    )
    assert heads == [previous]
    assert rig[1].get(first["task_id"])["data"]["revisions"] == 1
    assert rig[1].seen(first["task_id"], "review:doc")
    rig[1].set_meta("desktop_last_watch", "0")
    assert desktop.tick()["action"] == "wait"


@pytest.mark.parametrize("change", ["regression", "external_head"])
def test_revision_cannot_replace_original_regression_or_external_commits(rig, tmp_path, change):
    desktop = Desktop(rig[4])
    first = desktop.tick()
    complete(desktop, first, tmp_path)
    rig[2].state.events = [ReviewEvent(id="review:1", body="Please fix this.")]
    rig[1].set_meta("desktop_last_watch", "0")
    revision = desktop.tick()
    changes = {"calc.py": FIX + "\n# documentation\n", "tests/test_oss_regression_1.py": REGRESSION}
    if change == "regression":
        changes["tests/test_oss_regression_1.py"] += "\n# replacement\n"
    else:
        rig[2].state.head_sha = "c" * 40
    complete(desktop, revision, tmp_path, changes=changes)
    assert rig[1].get(first["task_id"])["status"] == "needs_attention"
    assert len(rig[2].published) == 1


def test_rate_limit_persists_backoff_without_model_or_chat(rig):
    def limited(options):
        raise RateLimited(123)

    rig[2].discover = limited
    assert Desktop(rig[4]).tick()["action"] == "wait"
    assert float(rig[1].meta("github_retry_after")) > time.time() + 100
    assert not rig[1].desktop_jobs()


def test_rate_limit_during_handoff_preserves_it_for_bounded_retry(rig, tmp_path):
    desktop = Desktop(rig[4])
    action = desktop.tick()
    original = rig[2].issue

    def limited(*args):
        raise RateLimited(120)

    rig[2].issue = limited
    result, path = complete(desktop, action, tmp_path)
    assert "backoff" in result["reason"]
    assert rig[1].desktop_job(action["ticket"])["state"] == "attached"
    assert float(rig[1].meta("github_retry_after")) > time.time() + 100
    assert not rig[2].published
    rig[2].issue = original
    rig[1].set_meta("github_retry_after", "0")
    desktop.finish(action["ticket"], path)
    assert len(rig[2].published) == 1


def test_installed_skill_is_bundled_and_existing_copy_is_preserved(tmp_path):
    target = install_skill(tmp_path / "skills", tmp_path / "config.toml")
    assert (target / "SKILL.md").is_file()
    assert json.loads((target / "installation.json").read_text())["config"] == str(
        tmp_path / "config.toml"
    )
    (target / "SKILL.md").write_text("user customization")
    with pytest.raises(ContributorError, match="already exists"):
        install_skill(tmp_path / "skills", tmp_path / "config.toml")
    assert (target / "SKILL.md").read_text() == "user customization"


def test_handoff_outside_job_directory_and_symlink_are_rejected(rig, tmp_path):
    desktop = Desktop(rig[4])
    action = desktop.tick()
    desktop.attach(action["ticket"], "native-chat-1")
    outside = tmp_path / "outside.json"
    outside.write_text(handoff(action).model_dump_json())
    with pytest.raises(ContributorError, match="assigned"):
        desktop.finish(action["ticket"], outside)
    Path(action["handoff_path"]).symlink_to(outside)
    with pytest.raises(ContributorError, match="assigned"):
        desktop.finish(action["ticket"], Path(action["handoff_path"]))
    assert not rig[2].published


def test_interrupted_validation_does_not_publish_a_worker_claim(rig):
    desktop = Desktop(rig[4])
    action = desktop.tick()
    desktop.attach(action["ticket"], "native-chat-1")
    desktop.confirm_stopped(action["ticket"], "native-chat-1")
    job = rig[1].desktop_job(action["ticket"])
    job["state"] = "validating"
    rig[1].save_desktop_job(job)
    desktop.tick(monitor_only=True)
    assert rig[1].get(action["task_id"])["status"] == "failed"
    assert not rig[2].published


def test_native_revision_limit_does_not_create_another_chat(rig, tmp_path):
    desktop = Desktop(rig[4])
    action = desktop.tick()
    complete(desktop, action, tmp_path)
    rig[1].update(action["task_id"], desktop_revision_attempts=2)
    rig[2].state.events = [ReviewEvent(id="review:3", body="Please fix this too.")]
    rig[1].set_meta("desktop_last_watch", "0")
    assert desktop.tick()["action"] == "wait"
    assert rig[1].get(action["task_id"])["status"] == "needs_attention"
    assert not rig[1].desktop_jobs()


def test_active_revision_requests_cancellation_when_pr_is_closed(rig, tmp_path):
    desktop = Desktop(rig[4])
    first = desktop.tick()
    complete(desktop, first, tmp_path)
    rig[2].state.events = [ReviewEvent(id="review:1", body="Please document this.")]
    rig[1].set_meta("desktop_last_watch", "0")
    revision = desktop.tick()
    old_thread = rig[1].desktop_job(first["ticket"])["data"]["thread_id"]
    with pytest.raises(ContributorError, match="fresh"):
        desktop.attach(revision["ticket"], old_thread)
    desktop.attach(revision["ticket"], "fresh-revision-chat")
    rig[2].state.state = "closed"
    result = desktop.tick()
    assert result["action"] == "cancel"
    assert result["thread_id"] == "fresh-revision-chat"
    desktop.confirm_stopped(revision["ticket"], "fresh-revision-chat")
    assert rig[1].get(first["task_id"])["status"] == "closed"


def test_ci_blocker_notifies_once_and_real_completion_is_recorded(rig, tmp_path):
    desktop = Desktop(rig[4])
    first = desktop.tick()
    complete(desktop, first, tmp_path)
    rig[1].acknowledge([n["id"] for n in rig[1].notifications()])
    rig[2].state.ci = [{"id": 1, "kind": "check", "status": "in_progress", "conclusion": None}]
    rig[1].set_meta("desktop_last_watch", "0")
    assert not desktop.tick()["notifications"]
    rig[2].state.ci[0].update(status="completed", conclusion="success")
    rig[1].set_meta("desktop_last_watch", "0")
    assert desktop.tick()["notifications"][0]["kind"] == "ci_completed"
    rig[1].acknowledge([n["id"] for n in rig[1].notifications()])
    rig[2].state.waiting = "failed CI logs unavailable"
    rig[1].set_meta("desktop_last_watch", "0")
    assert desktop.tick()["notifications"][0]["kind"] == "needs_attention"
    rig[1].acknowledge([n["id"] for n in rig[1].notifications()])
    rig[1].set_meta("desktop_last_watch", "0")
    assert not desktop.tick()["notifications"]


def test_notification_outbox_survives_restart_and_terminal_state_is_not_repeated(rig, tmp_path):
    desktop = Desktop(rig[4])
    action = desktop.tick()
    complete(desktop, action, tmp_path)
    rig[1].acknowledge([notice["id"] for notice in rig[1].notifications()])
    rig[2].state.state = "merged"
    rig[1].set_meta("desktop_last_watch", "0")
    desktop.tick(monitor_only=True)
    second = Store(rig[0])
    try:
        assert [notice["kind"] for notice in second.notifications()] == ["merged"]
        second.acknowledge([notice["id"] for notice in second.notifications()])
        assert not rig[1].notifications()
    finally:
        second.close()
    rig[1].set_meta("desktop_last_watch", "0")
    assert not desktop.tick(monitor_only=True)["notifications"]


def test_desktop_cli_setup_uses_empty_allowlist_and_real_native_tools(tmp_path):
    runner = CliRunner()
    config = tmp_path / "config.toml"
    assert runner.invoke(app, ["init", "-c", str(config)]).exit_code == 0
    config.write_text(
        "state_dir = " + json.dumps(str(tmp_path / "state")) + "\n" + config.read_text()
    )
    result = runner.invoke(app, ["desktop", "setup", "-c", str(config)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["allowlisted_repositories"] == 0
    assert "create_thread" in payload["required_app_tools"]
