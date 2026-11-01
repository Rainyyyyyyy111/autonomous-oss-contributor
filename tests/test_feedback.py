"""Description feedback must never require or authorize a source publication."""

from copy import deepcopy

import pytest
from conftest import FixtureSandbox
from test_desktop import complete
from test_engine import run_issue

from oss_contributor.desktop import Desktop
from oss_contributor.models import FeedbackPlan, Repair, ReviewEvent
from oss_contributor.process import ContributorError

EXPLANATION = Repair(
    root_cause="add(2, 3) returned -1 because add subtracted its operands.",
    summary="Add the operands so positive inputs return their sum.",
    evidence=["The independently verified patch changes subtraction to addition."],
)


def review(rig, kind="review"):
    event = ReviewEvent(
        id="description:1", kind=kind,
        body="Please shorten the PR description and remove the command transcript.",
    )
    rig[2].state.events = [event]
    rig[1].set_meta("desktop_last_watch", "0")
    return event


def revision(rig, tmp_path, kind="review"):
    desktop = Desktop(rig[4])
    first = desktop.tick()
    complete(desktop, first, tmp_path)
    review(rig, kind)
    return desktop, first, desktop.tick()


def test_pr_body_keeps_facts_template_and_disclosure_without_command_logs(rig):
    rig[2].issue_value.pr_template = "## Project checklist\n\n- Include a reproducer."
    task = run_issue(rig)
    body = rig[2].published[0]["body"]
    assert body.startswith(rig[2].issue_value.pr_template)
    assert "Fixes #1" in body
    assert "subtraction" in body
    assert "AI-assisted with Codex" in body
    assert "regression fails on the original code and passes with the fix" in body
    assert "Merge decisions" not in body
    assert "exit 1" not in body and "junitxml" not in body and "[\"python\"" not in body
    assert len(body) < 700
    assert any(c["code"] == 1 for c in task["data"]["report"]["commands"])
    rig[1].update(task["id"], report={"passed": False})
    with pytest.raises(ContributorError, match="independent validation"):
        rig[4].body(rig[1].get(task["id"]))


def test_desktop_description_revision_preserves_head_patch_report_and_budget(rig, tmp_path):
    desktop, first, action = revision(rig, tmp_path)
    before = deepcopy(rig[1].get(first["task_id"])["data"])
    containers = FixtureSandbox.created
    result, path = complete(
        desktop, action, tmp_path, outcome="description", changes={}, repair=EXPLANATION,
    )
    updated = rig[1].get(first["task_id"])
    assert updated["status"] == "open", updated["data"].get("reason")
    for key in ("pr", "changes", "report", "base_sha", "base_hashes", "regression_path"):
        assert updated["data"][key] == before[key]
    assert FixtureSandbox.created == containers
    assert len(rig[2].published) == len(rig[2].description_updates) == 1
    assert EXPLANATION.summary in rig[2].state.body
    assert rig[1].usage()["publications"] == 1
    assert updated["data"]["revisions"] == 1
    assert rig[1].seen(first["task_id"], "description:1")
    assert result["notifications"][-1]["kind"] == "pr_updated"
    desktop.finish(action["ticket"], path)
    assert len(rig[2].description_updates) == 1
    rig[1].set_meta("desktop_last_watch", "0")
    assert desktop.tick(monitor_only=True)["action"] == "wait"


@pytest.mark.parametrize("kind", ["ci", "mixed"])
def test_description_cannot_consume_failed_ci_feedback(rig, tmp_path, kind):
    desktop, first, action = revision(rig, tmp_path, "ci")
    if kind == "mixed":
        job = rig[1].desktop_job(action["ticket"])
        job["data"]["events"].append(ReviewEvent(id="review:2", body="Shorten description").model_dump())
        rig[1].save_desktop_job(job)
    complete(desktop, action, tmp_path, outcome="description", changes={}, repair=EXPLANATION)
    assert rig[1].get(first["task_id"])["status"] == "needs_attention"
    assert not rig[2].description_updates
    assert not rig[1].seen(first["task_id"], "description:1")


def test_description_outcome_rejects_hidden_file_changes_or_initial_submission(rig, tmp_path):
    from test_desktop import monitored_read

    desktop = Desktop(rig[4])
    first = desktop.tick()
    desktop.attach(first["ticket"], "native-description-rejection")
    monitored_read(desktop, first)
    with pytest.raises(ContributorError, match="requires a revision"):
        complete(desktop, first, tmp_path, outcome="description", changes={})
    complete(desktop, first, tmp_path)
    review(rig)
    action = desktop.tick()
    with pytest.raises(ContributorError, match="no file changes"):
        complete(desktop, action, tmp_path, outcome="description", repair=EXPLANATION)
    assert not rig[2].description_updates


@pytest.mark.parametrize("changed", ["head", "closed", "stop", "body", "new_review", "new_ci", "waiting"])
def test_description_revision_refuses_stale_or_stopped_work(rig, tmp_path, changed):
    desktop, first, action = revision(rig, tmp_path)
    if changed == "head":
        rig[2].state.head_sha = "c" * 40
    elif changed == "closed":
        rig[2].state.state = "closed"
    elif changed == "stop":
        rig[2].state.stop = True
    elif changed == "body":
        rig[2].state.body = "Maintainer edited the description."
    elif changed in {"new_review", "new_ci"}:
        rig[2].state.events.append(ReviewEvent(
            id="new:2", kind="ci" if changed == "new_ci" else "review", body="Fix the parser too.",
        ))
    else:
        rig[2].state.waiting = "CI pending"
    complete(desktop, action, tmp_path, outcome="description", changes={}, repair=EXPLANATION)
    assert rig[1].get(first["task_id"])["status"] in {"needs_attention", "closed", "stopped"}
    assert not rig[2].description_updates
    assert not rig[1].seen(first["task_id"], "description:1")


def test_description_patch_lost_response_recovers_without_source_publication(rig, tmp_path):
    desktop, first, action = revision(rig, tmp_path)
    patch = rig[2].update_pr_body

    def lost(*args):
        patch(*args)
        raise ContributorError("lost PATCH response")

    rig[2].update_pr_body = lost
    complete(desktop, action, tmp_path, outcome="description", changes={}, repair=EXPLANATION)
    assert rig[1].get(first["task_id"])["status"] == "publishing"
    assert not rig[1].seen(first["task_id"], "description:1")
    assert not rig[1].desktop_jobs()
    rig[2].update_pr_body = patch
    rig[1].update(first["task_id"], next_reconcile=0)
    rig[4].recover()
    assert rig[1].get(first["task_id"])["status"] == "open"
    assert rig[1].seen(first["task_id"], "description:1")
    assert len(rig[2].published) == len(rig[2].description_updates) == 1


def test_ambiguous_feedback_can_escalate_without_cosmetic_code_changes(rig, tmp_path):
    desktop, first, action = revision(rig, tmp_path)
    assert "ambiguous" in action["prompt"] and "AI slop" in action["prompt"]
    assert "previous_body" in action["prompt"]
    complete(
        desktop, action, tmp_path, outcome="needs_attention", changes={},
        reason="The broad AI-slop complaint does not identify description or code changes.",
    )
    assert rig[1].get(first["task_id"])["status"] == "needs_attention"
    assert len(rig[2].published) == 1
    assert not rig[2].description_updates


def test_standalone_classifies_and_updates_description_without_source_revision(rig):
    task = run_issue(rig)
    before = deepcopy(task["data"])
    review(rig)
    rig[3].feedback_plan = FeedbackPlan(
        action="description", reason="Only description wording requested.",
        evidence=["Maintainer explicitly requests a shorter PR description."], repair=EXPLANATION,
    )
    calls = len(rig[3].calls)
    rig[4].watch()
    updated = rig[1].get(task["id"])
    assert updated["status"] == "open", updated["data"].get("reason")
    assert rig[3].calls[calls:] == ["FeedbackPlan"]
    assert len(rig[2].published) == len(rig[2].description_updates) == 1
    for key in ("pr", "changes", "report", "base_sha", "base_hashes"):
        assert updated["data"][key] == before[key]


def test_standalone_ambiguous_review_is_escalated_without_edits(rig):
    task = run_issue(rig)
    report = deepcopy(task["data"]["report"])
    rig[2].state.events = [ReviewEvent(id="review:slop", body="Please edit AI slop out of the PR.")]
    rig[3].feedback_plan = FeedbackPlan(
        action="needs_attention", reason="Reviewer did not identify offending content.",
        evidence=["No line comments or specific passages supplied."],
    )
    rig[4].watch()
    assert rig[1].get(task["id"])["status"] == "needs_attention"
    assert rig[1].get(task["id"])["data"]["report"] == report
    assert not rig[2].description_updates
    assert len(rig[2].published) == 1


def test_standalone_description_retry_retains_independent_validation(rig):
    task = run_issue(rig)
    report = deepcopy(task["data"]["report"])
    review(rig)
    rig[3].feedback_plan = FeedbackPlan(
        action="description", reason="Description wording only.",
        evidence=["Explicit PR description request."], repair=EXPLANATION,
    )
    original = rig[2].update_pr_body

    def lost(*args):
        original(*args)
        raise ContributorError("lost response")

    rig[2].update_pr_body = lost
    rig[4].watch()
    interrupted = rig[1].get(task["id"])
    assert interrupted["status"] == "publishing"
    assert interrupted["data"]["report"] == report
    rig[2].update_pr_body = original
    rig[1].update(task["id"], next_reconcile=0)
    rig[4].recover()
    assert rig[1].get(task["id"])["status"] == "open"
    assert rig[1].seen(task["id"], "description:1")
    assert len(rig[2].published) == len(rig[2].description_updates) == 1


def test_description_noop_is_not_counted_as_addressed_feedback(rig, tmp_path):
    desktop, first, action = revision(rig, tmp_path)
    original = Repair.model_validate(rig[1].get(first["task_id"])["data"]["repair"])
    complete(desktop, action, tmp_path, outcome="description", changes={}, repair=original)
    assert rig[1].get(first["task_id"])["status"] == "needs_attention"
    assert not rig[2].description_updates
    assert not rig[1].seen(first["task_id"], "description:1")
