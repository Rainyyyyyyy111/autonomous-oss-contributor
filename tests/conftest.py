from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from oss_contributor.config import Config, Repository
from oss_contributor.engine import Engine
from oss_contributor.models import (
    Assessment,
    CommandResult,
    FeedbackPlan,
    Issue,
    NotificationBatch,
    PRState,
    Regression,
    Repair,
)
from oss_contributor.process import ContributorError
from oss_contributor.store import Store

BASE = {
    "calc.py": "def add(a, b):\n    return a - b\n",
    "tests/test_existing.py": "from calc import add\n\ndef test_zero():\n    assert add(0, 0) == 0\n",
}
FIX = "def add(a, b):\n    return a + b\n"
REGRESSION = "from calc import add\n\ndef test_add_positive():\n    assert add(2, 3) == 5\n"


class FixtureSandbox:
    """Runs ONLY trusted test fixtures locally. Never used by production CLI."""

    created = 0

    def __init__(self, repo, files, deadline):
        self.repo, self.files, self.deadline = repo, files, deadline

    def __enter__(self):
        type(self).created += 1
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.apply(self.files)
        return self

    def __exit__(self, *_args):
        self.temp.cleanup()

    def install(self):
        return []

    def apply(self, changes):
        for path, content in changes.items():
            file = self.root / path
            if content is None:
                file.unlink(missing_ok=True)
            else:
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(content)

    def snapshot(self):
        return {
            p.relative_to(self.root).as_posix(): p.read_text()
            for p in self.root.rglob("*")
            if p.is_file()
            and "__pycache__" not in p.parts
            and ".pytest_cache" not in p.parts
            and p.name != "oss-regression.xml"
        }

    def command(self, argv):
        if time.monotonic() > self.deadline:
            raise ContributorError("fixture deadline exceeded")
        args = list(argv)
        if args[0] in {"python", "/usr/local/bin/python"}:
            args[0] = sys.executable
        args = [
            a.replace("/tmp/oss-regression.xml", str(self.root / "oss-regression.xml"))
            for a in args
        ]
        result = subprocess.run(
            args,
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        return CommandResult(
            argv=argv, code=result.returncode, output=result.stdout + result.stderr
        )


class FakeGitHub:
    def __init__(self):
        self.files = dict(BASE)
        self.sha = "a" * 40
        self.issue_value = Issue(
            repo="owner/demo",
            number=1,
            title="Addition returns wrong result",
            body="Calling add(2, 3) returns -1, but it should return 5. Reproducible on main.",
        )
        self.published = []
        self.description_updates = []
        self.remote = None
        self.state = PRState(
            number=8, url="https://github.com/owner/demo/pull/8", state="open", head_sha="b" * 40
        )

    def discover(self, options):
        return [{"fullName": "outside/popular", "stargazersCount": 99999}]

    def issue_numbers(self, repo, limit):
        return [1]

    def issue_candidates(self, repo, limit, include_unlabelled=True):
        return [self.issue_value.model_dump()]

    def issue(self, repo, number):
        return self.issue_value.model_copy(deep=True)

    def base(self, repo):
        return "main", self.sha

    def snapshot(self, repo, sha):
        return dict(self.files)

    def find_pr(self, repo, branch):
        return self.remote

    def publish(self, repo, base_branch, sha, changes, branch, title, body, previous_head=None):
        if self.remote is None:
            self.published.append({"changes": changes, "body": body, "branch": branch, "sha": sha})
            self.remote = {"number": 8, "html_url": self.state.url, "state": "open"}
        self.state.body = body
        return {"number": 8, "url": self.state.url, "head_sha": self.state.head_sha}

    def update_pr_body(self, repo, number, body, expected_head, expected_body):
        assert self.state.head_sha == expected_head
        if self.state.body == body:
            return
        if self.state.body != expected_body:
            from oss_contributor.process import PublicationConflict

            raise PublicationConflict("PR description changed outside this agent")
        self.description_updates.append(body)
        self.state.body = body

    def pr_state(self, repo, number):
        return self.state

    def notification_batch(self, since, last_modified="", max_pages=10):
        return NotificationBatch(not_modified=True)


class FakeCodex:
    def __init__(self):
        self.calls = []
        self.bad_fix = False
        self.weaken = False
        self.unsuitable = False
        self.feedback_plan = None

    def ask(self, schema, prompt, sandbox, deadline, writable=False):
        self.calls.append(schema.__name__)
        if schema is FeedbackPlan:
            return self.feedback_plan or FeedbackPlan(
                action="code", reason="concrete function documentation request",
                evidence=["maintainer asks to document the function"],
            )
        if schema is Assessment:
            return Assessment(
                suitable=not self.unsuitable,
                reason="small reproducible arithmetic bug",
                evidence=["calc.add subtracts its operands"],
                reproduction="add(2,3)",
                expected_scope=["calc.py"],
                validation="pytest regression",
                root_cause="wrong operator",
            )
        if schema is Regression:
            return Regression(
                content=REGRESSION, explanation="positive operands expose subtraction"
            )
        if schema is Repair:
            if self.weaken:
                sandbox.apply({"tests/test_existing.py": "def test_nothing():\n    pass\n"})
            if not self.bad_fix:
                sandbox.apply({"calc.py": FIX})
            return Repair(
                summary="Use addition",
                root_cause="The implementation used subtraction.",
                evidence=["positive operand assertion passes"],
            )
        raise AssertionError(schema)


@pytest.fixture
def rig(tmp_path):
    config = Config(
        state_dir=tmp_path / "state",
        repositories=[Repository(name="owner/demo", install=[], contribution_policy_reviewed=True)],
    )
    store = Store(config)
    gh, codex = FakeGitHub(), FakeCodex()
    FixtureSandbox.created = 0
    engine = Engine(config, store, gh, codex, FixtureSandbox)
    yield config, store, gh, codex, engine
    store.close()
