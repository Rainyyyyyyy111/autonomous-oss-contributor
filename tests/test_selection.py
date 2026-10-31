import json
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from test_engine import run_issue

from oss_contributor.desktop import Desktop
from oss_contributor.github import GitHub
from oss_contributor.models import Issue
from oss_contributor.process import ContributorError
from oss_contributor.selection import Selector


def enqueue(rig, number, **evidence):
    task = rig[1].create_task(Issue(repo="owner/demo", number=number, title="A bug"))
    rig[1].discovery_evidence(task, evidence)
    return rig[1].get(task)


def test_rank_reproducer_above_feature_and_explain_decision(rig):
    feature = enqueue(rig, 1, title="New feature: redesign queries", body="Please rewrite this.")
    bug = enqueue(rig, 2, title="Bug", labels=["bug"],
                  body="Steps to reproduce: ```f(1)``` Expected 2, actual 0. AssertionError")
    queue = Selector(rig[1]).queue(persist=True)
    assert [t["id"] for t in queue] == [bug["id"], feature["id"]]
    assert queue[0]["selection"]["score"] == 60
    assert "probability" in queue[0]["selection"]["notice"]
    assert rig[1].get(bug["id"])["data"]["selection"]["signals"]
    assert rig[1].usage()["attempts"] == 0 and not rig[3].calls


def test_cooldown_uses_creation_time_and_does_not_spend_attempts(rig):
    created = datetime.now(UTC)
    task = enqueue(rig, 1, created_at=created.isoformat(), labels=["bug"])
    selection = Selector(rig[1]).evaluate(task, created.timestamp() + 60)
    assert not selection["ready"]
    assert selection["retry_at"] == created.timestamp() + 86400
    rig[4].process(task)
    assert Desktop(rig[4]).dispatch(task) is None
    assert not rig[3].calls and rig[1].usage()["attempts"] == 0
    assert Selector(rig[1]).evaluate(task, created.timestamp() + 86401)["ready"]
    rig[0].discovery.min_issue_age_hours = 0
    assert Selector(rig[1]).evaluate(task, created.timestamp() + 60)["ready"]


@pytest.mark.parametrize("evidence", [{"assignees": 1}, {"created_at": "bad"},
                                      {"created_at": "2020-01-01T00:00:00"}])
def test_uncertain_or_claimed_issue_is_deferred_before_dispatch(rig, evidence):
    task = enqueue(rig, 1, **evidence)
    assert not Selector(rig[1]).evaluate(task)["ready"]
    assert Desktop(rig[4]).dispatch(task) is None
    assert not rig[1].desktop_jobs() and rig[1].usage()["model_seconds"] == 0


def test_refresh_updates_search_evidence_without_reviving_terminal_task(rig):
    task = enqueue(rig, 1, assignees=1)
    rig[4].discover()
    task = rig[1].get(task["id"])
    assert Selector(rig[1]).evaluate(task)["ready"]
    rig[1].update(task["id"], "skipped")
    before = rig[1].get(task["id"])
    rig[4].discover()
    assert rig[1].get(task["id"]) == before


def test_native_auto_selection_uses_ranking_and_still_refreshes_conflicts(rig):
    enqueue(rig, 1, title="feature request")
    bug = enqueue(rig, 2, labels=["bug"], body="Expected 2, actual 0 ```f()```")
    rig[1].set_meta("last_discovery", str(time.time()))
    rig[2].issue_value.number = 2
    action = Desktop(rig[4]).tick()
    assert action["task_id"] == bug["id"]
    assert not rig[3].calls


def test_merge_bonus_is_capped_and_closed_without_feedback_is_neutral(rig):
    task = run_issue(rig)
    rig[2].state.state = "closed"
    rig[4].learning.observe(task, rig[2].state)
    next_task = enqueue(rig, 2)
    assert Selector(rig[1]).evaluate(next_task)["score"] == 0
    rig[2].state.state = "merged"
    rig[4].learning.observe(task, rig[2].state)
    assert Selector(rig[1]).evaluate(next_task)["score"] == 2
    assert len(rig[4].learning.outcomes()) == 1
    assert not rig[4].learning.records()


def test_search_bug_first_then_unlabelled_with_global_limit():
    gh = GitHub()
    queries = []

    def api(endpoint):
        args = parse_qs(urlparse(endpoint).query)
        queries.append(args)
        number = len(queries)
        return {"items": [{"number": number, "title": "bug", "body": "reproducer",
                           "labels": [{"name": "bug"}], "created_at": "2020-01-01T00:00:00Z"}]}

    gh.api = api
    rows = gh.issue_candidates("o/r", 2)
    assert [r["number"] for r in rows] == [1, 2]
    assert "label:bug" in queries[0]["q"][0]
    assert "-label:bug" in queries[1]["q"][0]
    assert queries[1]["per_page"] == ["1"]
    assert not rows[0]["ownership_checked"]
    assert json.dumps(rows)
    queries.clear()
    gh.issue_candidates("o/r", 2, False)
    assert len(queries) == 1


def test_incomplete_search_cannot_be_treated_as_full_evidence():
    gh = GitHub()
    gh.api = lambda *args: {"incomplete_results": True, "items": []}
    with pytest.raises(ContributorError, match="incomplete"):
        gh.issue_candidates("o/r", 10)


def test_outcomes_use_remote_timestamps_not_polling_time(rig):
    task = run_issue(rig)
    remote = rig[2].state
    remote.created_at = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    remote.merged_at = "2026-10-30T07:09:20Z"
    remote.state = "merged"
    rig[4].learning.observe(task, remote)
    rig[4].learning.observe(task, remote)
    outcomes = rig[4].learning.outcomes()
    assert len(outcomes) == 1 and outcomes[0]["merged_at"] == remote.merged_at
    assert rig[4].learning.report()["outcome_counts"] == {"merged": 1}
    assert rig[1].get(task["id"])["status"] == "open"  # evidence collection never transitions tasks
