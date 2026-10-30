from __future__ import annotations

import time
from datetime import UTC, datetime
from urllib.parse import urlencode

import pytest
from test_desktop import complete
from test_engine import run_issue

import oss_contributor.github as github_module
import oss_contributor.notifications as notification_module
from oss_contributor.desktop import Desktop
from oss_contributor.github import GitHub
from oss_contributor.models import CommandResult, NotificationBatch, ReviewEvent
from oss_contributor.notifications import NotificationWatcher
from oss_contributor.process import ContributorError, RateLimited


def setup(rig):
    task = run_issue(rig)
    rig[1].set_meta("github_notifications_last_sweep", str(time.time()))
    desktop = Desktop(rig[4])
    calls = []
    row = {
        "id": "thread-1", "updated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reason": "author", "repository": {"full_name": "owner/demo"},
        "subject": {"type": "PullRequest", "url": "https://api.github.com/repos/owner/demo/pulls/8",
                    "latest_comment_url": "https://evil.example/never-follow"},
    }

    def batch(*args):
        calls.append(args)
        return NotificationBatch(rows=[row], last_modified="Fri, 30 Oct 2026 10:00:00 GMT")

    rig[2].notification_batch = batch
    return task, row, calls, NotificationWatcher(desktop)


def force_poll(rig):
    rig[1].set_meta("github_notifications_next_poll", "0")


def test_feed_queues_review_without_model_publication_or_marking_review_seen(rig):
    task, row, calls, watcher = setup(rig)
    rig[2].state.events = [ReviewEvent(id="review:1", body="Please document add.")]
    model_calls = list(rig[3].calls)
    result = watcher.poll()
    assert result["decisions"] == [{"task": task["id"], "decision": "review_pending"}]
    assert rig[1].get(task["id"])["data"]["github_notification_pending"]
    assert not rig[1].seen(task["id"], "review:1")
    assert rig[3].calls == model_calls and len(rig[2].published) == 1
    assert not rig[1].desktop_jobs()
    notices = rig[1].notifications()
    force_poll(rig)
    watcher.poll()
    assert len(calls) == 2
    assert rig[1].notifications() == notices
    assert rig[1].db.execute("SELECT COUNT(*) FROM github_inbox").fetchone()[0] == 1


def test_native_tick_prioritizes_queued_review_before_periodic_sweep(rig):
    task, row, calls, watcher = setup(rig)
    rig[2].state.events = [ReviewEvent(id="review:1", body="Please document add.")]
    watcher.poll()
    rig[1].set_meta("desktop_last_watch", str(time.time()))
    result = watcher.desktop.tick(monitor_only=True)
    assert result["action"] == "dispatch"
    assert result["task_id"] == task["id"]
    assert len(calls) == 1
    assert not rig[1].get(task["id"])["data"]["github_notification_pending"]


@pytest.mark.parametrize("state", ["closed", "merged", "stop", "external_head", "pending", "ambiguous"])
def test_immediate_triage_uses_current_pr_state_instead_of_notification_reason(rig, state):
    task, row, calls, watcher = setup(rig)
    if state in {"closed", "merged"}:
        rig[2].state.state = state
        expected = state
    elif state == "stop":
        rig[2].state.stop = True
        expected = "stopped"
    elif state == "external_head":
        rig[2].state.head_sha = "c" * 40
        expected = "needs_attention"
    elif state == "pending":
        rig[2].state.waiting = "CI awaiting maintainer authorization"
        expected = "waiting"
    else:
        rig[2].state.events = [ReviewEvent(id="slop:1", body="Please edit AI slop out of the PR.")]
        expected = "needs_attention"
    assert watcher.poll()["decisions"] == [{"task": task["id"], "decision": expected}]
    assert not rig[1].desktop_jobs()
    assert len(rig[2].published) == 1


def test_explicit_description_feedback_is_queued_for_review_instead_of_guessed(rig):
    task, row, calls, watcher = setup(rig)
    rig[2].state.events = [ReviewEvent(id="slop:1", body="Remove AI slop from the PR description.")]
    assert watcher.poll()["decisions"][0]["decision"] == "review_pending"
    assert rig[1].get(task["id"])["status"] == "open"


@pytest.mark.parametrize("subject_type", ["Issue", "CheckSuite"])
def test_related_issue_and_ci_notifications_refresh_only_known_pr(rig, subject_type):
    task, row, calls, watcher = setup(rig)
    row["subject"] = {"type": subject_type, "url": "https://api.github.com/repos/owner/demo/issues/1"}
    rig[2].state.events = [ReviewEvent(id="ci:1", kind="ci", body="AssertionError on current head")]
    assert watcher.poll()["decisions"][0]["decision"] == "ci_failed"
    assert rig[1].get(task["id"])["data"]["github_notification_pending"]


@pytest.mark.parametrize("change", ["repo", "number", "url", "type"])
def test_unknown_notifications_never_expand_scope_or_follow_urls(rig, change):
    task, row, calls, watcher = setup(rig)
    if change == "repo":
        row["repository"]["full_name"] = "outside/repo"
    elif change == "number":
        row["subject"]["url"] = "https://api.github.com/repos/owner/demo/pulls/999"
    elif change == "url":
        row["subject"]["url"] = "https://evil.example/repos/owner/demo/pulls/8"
    else:
        row["subject"]["type"] = "Commit"

    def forbidden(*args):
        raise AssertionError("must not inspect an unrelated notification")

    rig[2].pr_state = forbidden
    assert not watcher.poll()["decisions"]
    assert rig[1].db.execute("SELECT COUNT(*) FROM github_inbox").fetchone()[0] == 0
    assert len(rig[1].tasks()) == 1


def test_poll_respects_server_interval_and_304_cursor(rig):
    task, row, calls, watcher = setup(rig)

    def batch(*args):
        calls.append(args)
        return NotificationBatch(not_modified=True, poll_interval=180,
                                 last_modified="Fri, 30 Oct 2026 10:00:00 GMT")

    rig[2].notification_batch = batch
    assert watcher.poll()["state"] == "not_modified"
    assert float(rig[1].meta("github_notifications_next_poll")) >= time.time() + 179
    assert watcher.poll()["state"] == "waiting"
    assert len(calls) == 1
    force_poll(rig)
    watcher.poll()
    assert calls[-1][1] == "Fri, 30 Oct 2026 10:00:00 GMT"
    assert calls[0][0] == calls[-1][0]  # The validator is bound to the same query.
    rig[2].notification_batch = lambda *args: NotificationBatch(not_modified=True)
    force_poll(rig)
    watcher.poll()
    assert rig[1].meta("github_notifications_last_modified") == "Fri, 30 Oct 2026 10:00:00 GMT"


def test_pending_observation_survives_feed_cursor_commit_and_failure(rig):
    task, row, calls, watcher = setup(rig)
    original = rig[2].pr_state
    rig[2].pr_state = lambda *args: (_ for _ in ()).throw(ContributorError("temporary read failure"))
    assert watcher.poll()["state"] == "error"
    cursor = rig[1].meta("github_notifications_since")
    assert cursor
    assert rig[1].db.execute("SELECT decision FROM github_inbox").fetchone()[0] == "pending"
    assert watcher.poll()["state"] == "error_backoff"
    rig[2].pr_state = original
    rig[2].state.state = "merged"
    force_poll(rig)
    assert watcher.poll()["decisions"][0]["decision"] == "merged"
    assert rig[1].get(task["id"])["status"] == "merged"


def test_fallback_sweep_catches_changes_even_without_any_notification(rig):
    task, row, calls, watcher = setup(rig)
    rig[1].set_meta("github_notifications_last_sweep", "0")
    rig[2].notification_batch = lambda *args: NotificationBatch(not_modified=True)
    rig[2].state.state = "closed"
    assert watcher.poll()["decisions"][0]["decision"] == "closed"


@pytest.mark.parametrize("state", ["paused", "backoff", "disabled"])
def test_pause_disabled_and_backoff_prevent_all_network(rig, state):
    task, row, calls, watcher = setup(rig)
    if state == "paused":
        rig[1].set_meta("paused", "user requested pause")
    elif state == "backoff":
        rig[1].set_meta("github_retry_after", str(time.time() + 100))
    else:
        rig[0].notifications.enabled = False
    assert watcher.poll()["state"] == state
    assert not calls


def test_rate_limit_preserves_cursor_and_sets_shared_backoff(rig):
    task, row, calls, watcher = setup(rig)
    rig[2].notification_batch = lambda *args: (_ for _ in ()).throw(RateLimited(125))
    assert watcher.poll()["state"] == "backoff"
    assert float(rig[1].meta("github_retry_after")) > time.time() + 124
    assert not rig[1].meta("github_notifications_since")


def test_notification_api_reads_all_including_read_and_uses_conditional_header(monkeypatch):
    commands = []

    def command(argv, **kwargs):
        commands.append(argv)
        return CommandResult(argv=argv, code=0,
                             output="HTTP/2.0 304 Not Modified\r\nX-Poll-Interval: 120\r\n\r\n")

    monkeypatch.setattr(github_module, "run_command", command)
    batch = GitHub().notification_batch("2026-10-30T00:00:00Z", "Fri, 30 Oct 2026 10:00:00 GMT")
    assert batch.not_modified and batch.poll_interval == 120
    assert "If-Modified-Since: Fri, 30 Oct 2026 10:00:00 GMT" in commands[0]
    endpoint = next(arg for arg in commands[0] if arg.startswith("notifications?"))
    assert "all=true" in endpoint and "per_page=50" in endpoint
    assert "--method" in commands[0] and commands[0][commands[0].index("--method") + 1] == "GET"


@pytest.mark.parametrize("link", ["valid", "foreign", "truncated"])
def test_notification_pagination_is_bounded_and_validates_next_link(monkeypatch, link):
    calls = []
    since = "2026-10-30T00:00:00Z"
    url = "https://api.github.com/notifications?" + urlencode({
        "all": "true", "participating": "false", "since": since, "per_page": 50, "page": 2,
    })
    if link == "foreign":
        url = url.replace("api.github.com", "evil.example")

    def command(argv, **kwargs):
        calls.append(argv)
        header = f'Link: <{url}>; rel="next"\r\n' if len(calls) == 1 else ""
        return CommandResult(argv=argv, code=0, output="HTTP/2.0 200 OK\r\n" + header + "\r\n[]")

    monkeypatch.setattr(github_module, "run_command", command)
    if link == "valid":
        assert not GitHub().notification_batch(since).rows
        assert len(calls) == 2
    else:
        with pytest.raises(ContributorError):
            GitHub().notification_batch(since, max_pages=1 if link == "truncated" else 10)
        assert len(calls) == 1


def test_local_alerts_are_deduplicated_and_never_acknowledge_app_notices(rig, monkeypatch):
    task, row, calls, watcher = setup(rig)
    rig[1].notify("local-alert", task=task["id"], kind="needs_attention", reason='$(touch /tmp/never) "x"')
    delivered = []
    monkeypatch.setattr(notification_module.platform, "system", lambda: "Darwin")

    def command(argv, **kwargs):
        delivered.append(argv)
        return CommandResult(argv=argv, code=0)

    monkeypatch.setattr(notification_module, "run_command", command)
    watcher.desktop_alerts()
    watcher.desktop_alerts()
    assert len(delivered) == 1
    assert "$(touch /tmp/never)" in delivered[0][-2]
    assert "$(touch /tmp/never)" not in delivered[0][2]
    assert any(n.get("reason") == '$(touch /tmp/never) "x"' for n in rig[1].notifications())


@pytest.mark.parametrize("body", ["Please edit AI slop out of the PR.", "Please fix the parser too."])
def test_new_feedback_cannot_be_bypassed_by_an_already_running_revision(rig, tmp_path, body):
    desktop = Desktop(rig[4])
    first = desktop.tick()
    complete(desktop, first, tmp_path)
    rig[2].state.events = [ReviewEvent(id="review:old", body="Please document add.")]
    rig[1].set_meta("desktop_last_watch", "0")
    revision = desktop.tick()
    rig[2].state.events.append(ReviewEvent(id="review:new", body=body))
    rig[1].set_meta("github_notifications_last_sweep", "0")
    NotificationWatcher(desktop).poll()
    task = rig[1].get(first["task_id"])
    changes = {**task["data"]["changes"], "calc.py": 'def add(a, b):\n    """Sum."""\n    return a + b\n'}
    complete(desktop, revision, tmp_path, changes=changes)
    assert len(rig[2].published) == 1
    assert rig[1].get(first["task_id"])["status"] == "needs_attention"
    assert not rig[1].seen(first["task_id"], "review:new")


@pytest.mark.parametrize("late_body,blocked", [("Fix the parser too.", True), ("Thanks!", False)])
def test_feedback_arriving_during_validation_blocks_source_publication(rig, tmp_path, late_body, blocked):
    desktop = Desktop(rig[4])
    first = desktop.tick()
    complete(desktop, first, tmp_path)
    rig[2].state.events = [ReviewEvent(id="review:old", body="Please document add.")]
    rig[1].set_meta("desktop_last_watch", "0")
    revision = desktop.tick()
    original = rig[4].validate

    def validate(*args, **kwargs):
        report = original(*args, **kwargs)
        rig[2].state.events.append(ReviewEvent(id="review:late", body=late_body))
        return report

    rig[4].validate = validate
    task = rig[1].get(first["task_id"])
    changes = {**task["data"]["changes"], "calc.py": 'def add(a, b):\n    """Sum."""\n    return a + b\n'}
    complete(desktop, revision, tmp_path, changes=changes)
    assert len(rig[2].published) == 1
    assert rig[1].get(first["task_id"])["status"] == ("needs_attention" if blocked else "open")
    assert not rig[1].seen(first["task_id"], "review:late")


def test_new_review_requests_confirmed_cancellation_before_another_worker(rig, tmp_path):
    desktop = Desktop(rig[4])
    first = desktop.tick()
    complete(desktop, first, tmp_path)
    rig[2].state.events = [ReviewEvent(id="review:old", body="Please document add.")]
    rig[1].set_meta("desktop_last_watch", "0")
    revision = desktop.tick()
    desktop.attach(revision["ticket"], "active-revision")
    rig[2].state.events.append(ReviewEvent(id="review:new", body="Fix the parser too."))
    result = desktop.tick(monitor_only=True)
    assert result["action"] == "cancel" and result["thread_id"] == "active-revision"
    assert rig[1].desktop_job(revision["ticket"])["state"] == "cancelling"
    assert len(rig[1].desktop_jobs()) == 1
    assert desktop.tick()["ticket"] == revision["ticket"]
