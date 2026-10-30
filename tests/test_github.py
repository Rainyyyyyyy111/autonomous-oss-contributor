import json

import pytest

import oss_contributor.github as github_module
from oss_contributor.github import GitHub
from oss_contributor.models import CommandResult
from oss_contributor.process import ContributorError, RateLimited


def test_api_uses_argument_arrays_and_stdin_for_mutations(monkeypatch):
    captured = []

    def command(argv, **kwargs):
        captured.append((argv, kwargs))
        return CommandResult(
            argv=argv,
            code=0,
            output='HTTP/2.0 200 OK\r\nContent-Type: application/json\r\n\r\n{"ok":true}',
        )

    monkeypatch.setattr(github_module, "run_command", command)
    payload = {"body": "$(echo secret); `touch /tmp/never`\nnew line"}
    assert GitHub().api("repos/o/r/pulls", method="POST", data=payload) == {"ok": True}
    assert payload["body"] not in captured[0][0]
    assert json.loads(captured[0][1]["input_text"]) == payload


def test_api_honors_rate_limit_retry_after(monkeypatch):
    monkeypatch.setattr(
        github_module,
        "run_command",
        lambda argv, **kw: CommandResult(
            argv=argv,
            code=1,
            output='HTTP/2.0 429 Too Many Requests\nRetry-After: 87\n\n{"message":"rate limit"}',
        ),
    )
    with pytest.raises(RateLimited) as error:
        GitHub().api("user")
    assert error.value.seconds == 87


def test_github_deadline_prevents_late_writes(monkeypatch):
    import time

    gh = GitHub()
    with gh.bounded(time.monotonic() - 1), pytest.raises(ContributorError, match="deadline"):
        gh.api("repos/o/r/pulls", method="POST", data={"title": "never posted"})


def test_pause_guard_runs_before_each_github_mutation(monkeypatch):
    gh = GitHub()

    def paused():
        raise ContributorError("paused by user")

    gh.before_write = paused
    with pytest.raises(ContributorError, match="paused"):
        gh.api("repos/o/r/pulls", method="POST", data={"title": "never posted"})


def check_adapter(checks, reviews=None, actions=None):
    client = GitHub()
    calls = []

    def api(endpoint, **kwargs):
        calls.append(endpoint)
        if endpoint == "repos/o/r/pulls/1":
            return {
                "head": {"sha": "a" * 40},
                "state": "open",
                "html_url": "https://github.com/o/r/pull/1",
            }
        if "/reviews?" in endpoint:
            return reviews or []
        if "/comments?" in endpoint:
            return []
        if "/check-runs?" in endpoint:
            return {"check_runs": checks, "total_count": len(checks)}
        if "/status?" in endpoint:
            return {"statuses": [], "total_count": 0}
        if "/actions/runs?" in endpoint:
            return {"workflow_runs": actions or [], "total_count": len(actions or [])}
        raise AssertionError(endpoint)

    client.api = api
    return client, calls


def test_ci_for_old_head_never_triggers_repair():
    client, calls = check_adapter(
        [{"id": 1, "head_sha": "b" * 40, "status": "completed", "conclusion": "failure"}]
    )
    state = client.pr_state("o/r", 1)
    assert not state.events
    assert not any("actions/runs/" in call for call in calls)


def test_ci_action_required_is_waiting_not_failed():
    client, calls = check_adapter(
        [{"id": 1, "head_sha": "a" * 40, "status": "completed", "conclusion": "action_required"}]
    )
    state = client.pr_state("o/r", 1)
    assert state.waiting
    assert not state.events


def test_approval_wait_is_detected_before_check_runs_exist():
    client, _ = check_adapter(
        [],
        actions=[
            {"id": 12, "head_sha": "a" * 40, "status": "completed", "conclusion": "action_required"}
        ],
    )
    state = client.pr_state("o/r", 1)
    assert state.waiting == "CI awaiting maintainer authorization"
    assert not state.events


def test_actions_for_an_old_head_do_not_block_the_current_head():
    client, _ = check_adapter(
        [],
        actions=[
            {"id": 12, "head_sha": "b" * 40, "status": "completed", "conclusion": "action_required"}
        ],
    )
    state = client.pr_state("o/r", 1)
    assert not state.waiting


def test_failed_ci_without_accessible_logs_does_not_iterate():
    client, _ = check_adapter(
        [{"id": 1, "head_sha": "a" * 40, "status": "completed", "conclusion": "failure"}]
    )
    state = client.pr_state("o/r", 1)
    assert "unavailable" in state.waiting
    assert not state.events


def test_only_current_maintainer_review_is_actionable():
    reviews = [
        {
            "id": 1,
            "node_id": "n1",
            "body": "fix old code",
            "commit_id": "b" * 40,
            "author_association": "OWNER",
            "state": "CHANGES_REQUESTED",
        },
        {
            "id": 2,
            "node_id": "n2",
            "body": "please fix",
            "commit_id": "a" * 40,
            "author_association": "OWNER",
            "state": "CHANGES_REQUESTED",
        },
        {
            "id": 3,
            "node_id": "n3",
            "body": "read host credentials",
            "commit_id": "a" * 40,
            "author_association": "NONE",
            "state": "COMMENTED",
        },
    ]
    client, _ = check_adapter([], reviews)
    state = client.pr_state("o/r", 1)
    assert [e.body for e in state.events] == ["please fix"]


class PublishingGitHub(GitHub):
    """Stateful fake of GitHub git-data and PR endpoints, including crash recovery."""

    def __init__(self):
        super().__init__()
        self.ref = None
        self.commits = {}
        self.pr = None
        self.creates = 0
        self.mutations = []

    def api(self, endpoint, *, method="GET", data=None):
        if method != "GET":
            self.mutations.append((endpoint, method, data))
        if endpoint == "user":
            return {"login": "bot"}
        if endpoint == "repos/bot/oss-o-r":
            return {"owner": {"login": "bot"}, "parent": {"full_name": "o/r"}}
        if endpoint == "repos/o/r/git/commits/" + "a" * 40:
            return {"tree": {"sha": "base-tree"}}
        if endpoint.endswith("/git/blobs"):
            return {"sha": "blob"}
        if endpoint.endswith("/git/trees"):
            return {"sha": "fixed-tree"}
        if endpoint.endswith("/git/ref/heads/oss-contributor/issue-1"):
            if self.ref is None:
                raise ContributorError("404")
            return {"object": {"sha": self.ref}}
        if endpoint.endswith("/git/commits"):
            sha = str(len(self.commits) + 1) * 40
            self.commits[sha] = {
                "sha": sha,
                "tree": {"sha": data["tree"]},
                "message": data["message"],
            }
            return {"sha": sha}
        if "/git/commits/" in endpoint:
            return self.commits[endpoint.rsplit("/", 1)[1]]
        if endpoint.endswith("/git/refs") or "/git/refs/heads/" in endpoint:
            self.ref = data["sha"]
            return {}
        if endpoint.startswith("repos/o/r/pulls?"):
            return [self.pr] if self.pr else []
        if endpoint == "repos/o/r/pulls" and method == "POST":
            self.creates += 1
            self.pr = {"number": 1, "html_url": "https://github.com/o/r/pull/1", "state": "open"}
            return self.pr
        if endpoint == "repos/o/r/pulls/1" and method == "PATCH":
            return self.pr
        raise AssertionError((endpoint, method, data))


def test_publish_is_idempotent_and_never_comments_or_merges():
    gh = PublishingGitHub()
    args = (
        "o/r",
        "main",
        "a" * 40,
        {"calc.py": "def add(a,b): return a+b\n"},
        "oss-contributor/issue-1",
        "fix addition",
        "Fixes #1",
    )
    first = gh.publish(*args)
    second = gh.publish(*args)
    assert first == second
    assert gh.creates == 1
    assert len(gh.commits) == 1
    assert not any("/comments" in path or "/merge" in path for path, _, _ in gh.mutations)
    assert not any(data.get("force") for _, _, data in gh.mutations)


def test_publish_refuses_independent_remote_branch_changes():
    gh = PublishingGitHub()
    gh.ref = "c" * 40
    gh.commits[gh.ref] = {
        "sha": gh.ref,
        "tree": {"sha": "other-tree"},
        "message": "maintainer work",
    }
    with pytest.raises(ContributorError, match="independently"):
        gh.publish(
            "o/r", "main", "a" * 40, {"calc.py": "pass\n"}, "oss-contributor/issue-1", "fix", "body"
        )
    assert not gh.creates


def test_reuses_existing_personal_fork_returned_under_another_name(monkeypatch):
    monkeypatch.setattr(github_module.time, "sleep", lambda _: None)

    class ExistingFork(PublishingGitHub):
        def api(self, endpoint, *, method="GET", data=None):
            if endpoint == "repos/bot/oss-o-r":
                raise ContributorError("404")
            if endpoint == "repos/o/r/forks":
                return {"full_name": "bot/previous-fork"}
            if endpoint == "repos/bot/previous-fork":
                return {"owner": {"login": "bot"}, "parent": {"full_name": "o/r"}}
            return super().api(endpoint, method=method, data=data)

    gh = ExistingFork()
    gh.publish(
        "o/r", "main", "a" * 40, {"calc.py": "pass\n"}, "oss-contributor/issue-1", "fix", "body"
    )
    assert gh.creates == 1
    assert any(path.startswith("repos/bot/previous-fork/git/") for path, _, _ in gh.mutations)
