from __future__ import annotations

import time

import pytest
from conftest import BASE, FIX, REGRESSION, FixtureSandbox

from oss_contributor.models import ReviewEvent
from oss_contributor.process import ContributorError, QuotaExhausted, RateLimited


def run_issue(rig):
    config, store, gh, codex, engine = rig
    task_id = store.create_task(gh.issue_value)
    engine.process(store.get(task_id))
    return store.get(task_id)


def test_end_to_end_real_regression_and_clean_verification(rig):
    _, store, gh, _, _ = rig
    task = run_issue(rig)
    assert task["status"] == "open", task["data"].get("reason")
    report = task["data"]["report"]
    assert report["passed"]
    assert [r["code"] for r in report["commands"]] == [0, 1, 0, 0]
    assert FixtureSandbox.created == 2
    assert gh.published[0]["changes"]["calc.py"] == FIX
    assert "prepared automatically" in gh.published[0]["body"]
    assert store.usage()["attempts"] == store.usage()["publications"] == 1


@pytest.mark.parametrize("mode", ["bad_fix", "weaken", "unsuitable"])
def test_invalid_changes_never_publish(rig, mode):
    rig[3].__dict__[mode] = True
    task = run_issue(rig)
    assert task["status"] in {"failed", "skipped"}
    assert not rig[2].published


def test_baseline_failure_never_calls_model(rig):
    rig[2].files["tests/test_existing.py"] = "def test_broken():\n    assert False\n"
    task = run_issue(rig)
    assert task["status"] == "failed"
    assert not rig[3].calls
    assert not rig[2].published


def test_white_list_is_authorization(rig):
    rig[0].repositories.clear()
    task = run_issue(rig)
    assert task["status"] == "skipped"
    assert not rig[3].calls
    assert not rig[2].published


def test_discovery_never_enqueues_outside_allowlist(rig):
    rig[4].discover()
    assert [t["repo"] for t in rig[1].tasks()] == ["owner/demo"]
    assert not rig[3].calls


@pytest.mark.parametrize(
    "conflict", ["linked open PR", "issue has an assignee", "ownership uncertain"]
)
def test_conflicts_are_rejected_before_spending_quota(rig, conflict):
    rig[2].issue_value.conflicts = [conflict]
    assert run_issue(rig)["status"] == "skipped"
    assert rig[1].usage()["attempts"] == 0


def test_prepublication_conflict_never_forks(rig):
    gh = rig[2]
    original = gh.issue
    calls = 0

    def issue(repo, number):
        nonlocal calls
        calls += 1
        result = original(repo, number)
        if calls >= 2:
            result.conflicts = ["another contributor opened a PR"]
        return result

    gh.issue = issue
    task = run_issue(rig)
    assert task["status"] == "needs_attention"
    assert not gh.published


def test_pause_prevents_all_new_work(rig):
    rig[1].set_meta("paused", "by user")
    rig[4].once()
    assert not rig[1].tasks()
    assert not rig[3].calls


def test_attempt_budget_is_durable(rig):
    store = rig[1]
    for _ in range(3):
        assert store.reserve("attempts")
    assert not store.reserve("attempts")
    assert run_issue(rig)["status"] == "queued"


def test_publication_budget_keeps_verified_artifact(rig):
    assert rig[1].reserve("publications")
    task = run_issue(rig)
    assert task["status"] == "validated"
    assert task["data"]["report"]["passed"]
    assert not rig[2].published


def test_restart_reconciles_create_before_save_without_duplicate_pr(rig):
    task = run_issue(rig)
    store, gh, engine = rig[1], rig[2], rig[4]
    store.update(task["id"], "publishing", pr=None)
    engine.recover()
    assert store.get(task["id"])["status"] == "open"
    assert len(gh.published) == 1
    assert store.usage()["publications"] == 1


def test_interrupted_unverified_work_is_not_retried(rig):
    store, engine = rig[1], rig[4]
    ident = store.create_task(rig[2].issue_value)
    store.update(ident, "running")
    engine.recover()
    assert store.get(ident)["status"] == "failed"
    assert not rig[3].calls


def test_ci_pending_does_not_trigger_revision(rig):
    task = run_issue(rig)
    rig[2].state.waiting = "CI awaiting maintainer authorization"
    rig[2].state.events = [ReviewEvent(id="ci:1", kind="ci", body="not authorized")]
    calls = len(rig[3].calls)
    rig[4].watch()
    assert len(rig[3].calls) == calls
    assert rig[1].get(task["id"])["data"]["waiting"]


def test_external_head_change_stops_automatic_revisions(rig):
    task = run_issue(rig)
    rig[2].state.head_sha = "c" * 40
    rig[2].state.events = [ReviewEvent(id="review:1", body="fix this")]
    rig[4].watch()
    assert rig[1].get(task["id"])["status"] == "needs_attention"


@pytest.mark.parametrize("state", ["merged", "closed"])
def test_maintainer_decision_is_recorded(rig, state):
    task = run_issue(rig)
    rig[2].state.state = state
    rig[4].watch()
    assert rig[1].get(task["id"])["status"] == state


def test_maintainer_stop_is_terminal(rig):
    task = run_issue(rig)
    rig[2].state.stop = True
    rig[4].watch()
    assert rig[1].get(task["id"])["status"] == "stopped"


def test_processed_review_is_not_repeated(rig):
    task = run_issue(rig)
    rig[1].mark_seen(task["id"], ["review:1"])
    rig[2].state.events = [ReviewEvent(id="review:1", body="fix this")]
    calls = len(rig[3].calls)
    rig[4].watch()
    assert len(rig[3].calls) == calls


def test_revision_limit_is_terminal_for_auto_updates(rig):
    task = run_issue(rig)
    rig[1].update(task["id"], revisions=2)
    rig[2].state.events = [ReviewEvent(id="review:1", body="fix this")]
    rig[4].watch()
    assert rig[1].get(task["id"])["status"] == "needs_attention"


def test_unchanged_revision_is_not_published(rig):
    task = run_issue(rig)
    rig[2].state.events = [ReviewEvent(id="review:1", body="thanks, looks fine")]
    rig[4].watch()
    assert rig[1].get(task["id"])["status"] == "needs_attention"
    assert len(rig[2].published) == 1


def test_timeout_never_publishes(rig, monkeypatch):
    def timeout(*_args, **_kwargs):
        raise ContributorError("task deadline exceeded")

    monkeypatch.setattr(FixtureSandbox, "command", timeout)
    assert run_issue(rig)["status"] == "failed"
    assert not rig[2].published


def test_quota_exhaustion_pauses_model_work(rig):
    def exhaust(*_args, **_kwargs):
        raise QuotaExhausted("quota exhausted")

    rig[3].ask = exhaust
    run_issue(rig)
    assert "quota" in rig[1].meta("paused").lower()
    assert not rig[2].published


def test_rate_limit_is_persisted_without_blocking_sleep(rig):
    def limit(_options):
        raise RateLimited(123)

    rig[2].discover = limit
    rig[4].once()
    assert float(rig[1].meta("github_retry_after")) > time.time() + 100


def test_updated_upstream_requires_fresh_validation(rig):
    gh = rig[2]
    original = gh.base
    calls = 0

    def base(repo):
        nonlocal calls
        calls += 1
        if calls >= 2:
            gh.sha = "c" * 40
        return original(repo)

    gh.base = base
    task = run_issue(rig)
    assert task["status"] == "open", task["data"].get("reason")
    assert task["data"]["report"]["base_sha"] == "c" * 40
    assert FixtureSandbox.created == 3


def test_upstream_change_to_affected_file_blocks_old_patch(rig):
    gh = rig[2]
    original = gh.base
    calls = 0

    def base(repo):
        nonlocal calls
        calls += 1
        if calls >= 2:
            gh.sha = "c" * 40
            gh.files["calc.py"] += "\n# independently changed\n"
        return original(repo)

    gh.base = base
    run_issue(rig)
    assert not gh.published


def test_model_cannot_self_certify_wrong_patch(rig):
    path = "tests/test_oss_regression_1.py"
    wrong = {"calc.py": BASE["calc.py"] + "\n# claimed fixed\n", path: REGRESSION}
    report = rig[4].validate(
        rig[0].repositories[0], BASE, wrong, path, "a" * 40, time.monotonic() + 30
    )
    assert not report.passed
    assert "did not pass" in report.failure


def test_successful_revision_is_reverified_and_event_persisted(rig):
    from oss_contributor.models import Repair

    task = run_issue(rig)
    original = rig[3].ask

    def revised(schema, prompt, sandbox, deadline, writable=False):
        result = original(schema, prompt, sandbox, deadline, writable)
        if schema is Repair:
            sandbox.apply(
                {"calc.py": 'def add(a, b):\n    """Return the sum."""\n    return a + b\n'}
            )
        return result

    rig[3].ask = revised
    rig[2].state.events = [ReviewEvent(id="review:docstring", body="Please document add.")]
    rig[4].watch()
    updated = rig[1].get(task["id"])
    assert updated["status"] == "open", updated["data"].get("reason")
    assert updated["data"]["revisions"] == 1
    assert updated["data"]["report"]["passed"]
    assert rig[1].seen(task["id"], "review:docstring")
    assert rig[1].usage()["publications"] == 1


def test_publication_interruption_preserves_intent_and_recovers(rig):
    original = rig[2].publish

    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise ContributorError("connection lost after PR creation")

    rig[2].publish = interrupted
    task = run_issue(rig)
    assert task["status"] == "publishing"
    rig[2].publish = original
    rig[1].update(task["id"], next_reconcile=0)
    rig[4].recover()
    assert rig[1].get(task["id"])["status"] == "open"
    assert len(rig[2].published) == 1


def test_publication_reservation_is_renewed_after_local_midnight(rig, monkeypatch):
    task = run_issue(rig)
    store = rig[1]
    store.update(task["id"], "publishing", pr=None)
    rig[2].remote = None
    monkeypatch.setattr(store, "day", lambda: "2099-01-02")
    rig[4].recover()
    assert store.get(task["id"])["data"]["publication_reserved"] == "2099-01-02"
    assert store.usage()["publications"] == 1


def test_stopped_but_open_pr_still_counts_toward_open_limits(rig):
    task = run_issue(rig)
    rig[1].update(task["id"], "stopped")
    assert task["id"] in [t["id"] for t in rig[4].active()]


def test_closed_pr_during_publication_recovery_is_not_replaced(rig):
    task = run_issue(rig)
    rig[1].update(task["id"], "publishing", pr=None)
    rig[2].remote["state"] = "closed"
    rig[4].recover()
    assert rig[1].get(task["id"])["status"] == "closed"
    assert len(rig[2].published) == 1
