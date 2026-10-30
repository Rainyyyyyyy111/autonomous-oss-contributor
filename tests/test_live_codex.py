import os

import pytest

from oss_contributor.codex import Codex
from oss_contributor.engine import Engine
from oss_contributor.sandbox import DockerSandbox

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("OSS_TEST_LIVE_CODEX") != "1",
        reason="opt in with OSS_TEST_LIVE_CODEX=1; consumes account usage",
    ),
]


def test_real_codex_and_docker_complete_fixture_without_github_writes(rig):
    config, store, github, _, _ = rig
    config.limits.task_seconds = 180
    config.model = os.environ.get("OSS_TEST_CODEX_MODEL") or config.model
    engine = Engine(
        config, store, github=github, codex=Codex(config), sandbox_factory=DockerSandbox
    )
    ident = store.create_task(github.issue_value)
    engine.process(store.get(ident))
    task = store.get(ident)
    assert task["status"] == "open", task["data"].get("reason")
    assert task["data"]["report"]["passed"]
    assert len(github.published) == 1
