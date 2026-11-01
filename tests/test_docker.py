import json
import os
import time

import pytest
from conftest import BASE, FIX, REGRESSION

from oss_contributor.config import Config, Repository
from oss_contributor.desktop import Desktop, Handoff
from oss_contributor.engine import Engine
from oss_contributor.environments import Environments
from oss_contributor.errors import TransientFailure
from oss_contributor.models import Assessment, Repair
from oss_contributor.process import ContributorError
from oss_contributor.retries import RetryPolicy
from oss_contributor.sandbox import DockerSandbox
from oss_contributor.store import Store
from oss_contributor.tool_guard import ExecutionLimitExceeded, ToolPolicy
from oss_contributor.worker_tools import WorkerTools

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(
        os.environ.get("OSS_TEST_DOCKER") != "1",
        reason="set OSS_TEST_DOCKER=1 to run Docker integration",
    ),
]


def test_prepare_runner_uses_fresh_empty_isolated_container(tmp_path):
    config = Config(state_dir=tmp_path / "state")
    store = Store(config)
    try:
        manager = Environments(store)
        repo = Repository(name="owner/fixture", image=os.environ.get("OSS_TEST_RUNNER_IMAGE", "oss-contributor-runner:0.1"), install=[])
        result = manager.prepare(repo)
        assert result["ok"], result
        assert result["image_id"].startswith("sha256:")
        assert manager.prepare(repo) == result
        assert not store.tasks() and store.usage()["attempts"] == 0
    finally:
        store.close()


def test_real_docker_independent_red_green_verification(tmp_path):
    config = Config(state_dir=tmp_path / "state")
    store = Store(config)
    repo = Repository(name="owner/fixture", image=os.environ.get("OSS_TEST_RUNNER_IMAGE", "oss-contributor-runner:0.1"), install=[])
    try:
        path = "tests/test_oss_regression_1.py"
        report = Engine(config, store).validate(
            repo, BASE, {"calc.py": FIX, path: REGRESSION}, path, "a" * 40, time.monotonic() + 90
        )
        assert report.passed, report.failure
        assert [r.code for r in report.commands] == [0, 1, 0, 0]
    finally:
        store.close()


def test_real_docker_isolation_traversal_symlinks_and_offline_network():
    repo = Repository(name="owner/fixture", image=os.environ.get("OSS_TEST_RUNNER_IMAGE", "oss-contributor-runner:0.1"), install=[])
    files = {**BASE, "fixtures/large.txt": "large fixture\n" * 60_000}
    with DockerSandbox(repo, files, time.monotonic() + 90) as box:
        box.assert_isolated()
        assert box.snapshot()["fixtures/large.txt"] == files["fixtures/large.txt"]
        for path in ("../auth.json", "/etc/passwd", "x/../../auth.json"):
            with pytest.raises(ContributorError):
                box.tool("repo_read", {"path": path})
        with pytest.raises(ContributorError):
            box.tool("host_exec", {"argv": ["cat", "~/.codex/auth.json"]})
        with pytest.raises(ContributorError):
            box.tool("repo_apply", {"changes": {"tests/test_existing.py": "pass"}}, writable=True)
        secret_check = box.command(
            [
                "python",
                "-c",
                "import os; print([k for k in os.environ if 'TOKEN' in k or 'API_KEY' in k])",
            ]
        )
        assert json.loads(secret_check.output) == []
        assert (
            box.command(
                [
                    "python",
                    "-c",
                    "from pathlib import Path; print(Path('/var/run/docker.sock').exists())",
                ]
            ).output.strip()
            == "False"
        )
        network = box.command(
            ["python", "-c", "import socket; socket.create_connection(('1.1.1.1',443),timeout=1)"]
        )
        assert network.code != 0
        box.command(["python", "-c", "import os; os.symlink('/etc/passwd','escape')"])
        with pytest.raises(ContributorError, match="rejected"):
            box.tool("repo_read", {"path": "escape"})


def test_native_handoff_uses_real_clean_docker_without_cli_model(rig):
    from pathlib import Path

    config, store, github, codex, _ = rig
    config.limits.task_seconds = 90
    config.repositories[0].image = os.environ.get("OSS_TEST_RUNNER_IMAGE", config.repositories[0].image)
    desktop = Desktop(Engine(config, store, github, codex, DockerSandbox))
    action = desktop.tick()
    assert action["action"] == "dispatch"
    desktop.attach(action["ticket"], "fixture-native-chat")
    with WorkerTools(action["job_path"], github=github) as worker:
        worker.tool("repo_read", {"path": "calc.py"})
    desktop.confirm_stopped(action["ticket"], "fixture-native-chat")
    handoff = Handoff(
        ticket=action["ticket"], outcome="fixed",
        assessment=Assessment(suitable=True, reason="small arithmetic bug", evidence=["wrong operator"],
                              reproduction="add(2,3)", expected_scope=["calc.py"], validation="pytest"),
        repair=Repair(summary="Use addition", root_cause="subtraction operator", evidence=["regression"]),
        changes={"calc.py": FIX, "tests/test_oss_regression_1.py": REGRESSION},
    )
    path = Path(action["handoff_path"])
    path.write_text(handoff.model_dump_json())
    desktop.finish(action["ticket"], path)
    task = store.get(action["task_id"])
    assert task["status"] == "open", task["data"].get("reason")
    assert [cmd["code"] for cmd in task["data"]["report"]["commands"]] == [0, 1, 0, 0]
    assert not codex.calls
    assert len(github.published) == 1  # GitHub boundary remains simulated.


def test_native_guard_observes_real_docker_edits_and_stops_repetition(rig):
    config, store, github, codex, _ = rig
    config.limits.task_seconds = 90
    config.limits.tools = ToolPolicy(max_repeated_calls=2)
    config.repositories[0].image = os.environ.get("OSS_TEST_RUNNER_IMAGE", config.repositories[0].image)
    desktop = Desktop(Engine(config, store, github, codex, DockerSandbox))
    action = desktop.tick()
    desktop.attach(action["ticket"], "docker-guard-fixture")
    with WorkerTools(action["job_path"], github=github) as worker:
        worker.tool("repo_read", {"path": "calc.py"})
        worker.tool("repo_read", {"path": "calc.py"})
        worker.tool("regression_write", {"content": REGRESSION})
        worker.tool("repo_apply", {"changes": {"calc.py": FIX}})
        assert worker.tool("repo_read", {"path": "calc.py"}) == FIX
        assert worker.tool("sandbox_exec", {"argv": ["python", "-m", "pytest", "-q"]})["code"] == 0
        worker.tool("repo_read", {"path": "calc.py"})
        with pytest.raises(ExecutionLimitExceeded, match="repeated"):
            worker.tool("repo_read", {"path": "calc.py"})
    assert desktop.tick()["action"] == "cancel"
    assert not github.published and not codex.calls
    desktop.confirm_stopped(action["ticket"], "docker-guard-fixture")
    assert not store.desktop_jobs()


def test_native_safe_read_retries_once_with_real_docker_and_counts_it(rig, monkeypatch):
    config, store, github, codex, _ = rig
    config.limits.task_seconds = 90
    config.limits.retries = RetryPolicy(base_delay_seconds=0)
    config.repositories[0].image = os.environ.get("OSS_TEST_RUNNER_IMAGE", config.repositories[0].image)
    desktop = Desktop(Engine(config, store, github, codex, DockerSandbox))
    action = desktop.tick()
    desktop.attach(action["ticket"], "docker-read-retry-fixture")
    with WorkerTools(action["job_path"], github=github) as worker:
        original = worker.box.tool
        calls = []

        def transient_read(name, arguments, writable=False):
            calls.append(name)
            if len(calls) == 1:
                raise TransientFailure("synthetic transport failure before actual Docker read")
            return original(name, arguments, writable=writable)

        monkeypatch.setattr(worker.box, "tool", transient_read)
        assert worker.tool("repo_read", {"path": "calc.py"}) == BASE["calc.py"]
    activity = store.desktop_job(action["ticket"])["data"]["tool_activity"]
    assert calls == ["repo_read", "repo_read"]
    assert activity["steps"] == 3 and activity["retries_used"] == 1
    desktop.confirm_stopped(action["ticket"], "docker-read-retry-fixture")
    desktop.fail(action["ticket"], "fixture ended without a repair")
    assert not github.published and not codex.calls and not store.desktop_jobs()


def test_installation_network_allows_pypi_and_denies_other_hosts(monkeypatch):
    image = os.environ.get("OSS_TEST_RUNNER_IMAGE", "oss-contributor-runner:0.1")
    if image != "oss-contributor-runner:0.1":
        # Same runtime Dockerfile, private test tag for both worker and proxy.
        # This avoids retagging or replacing the operator's production proxy image.
        original = DockerSandbox.docker

        def private_image(self, *args, **kwargs):
            return original(self, *(image if arg == "oss-contributor-runner:0.1" else arg for arg in args), **kwargs)

        monkeypatch.setattr(DockerSandbox, "docker", private_image)
    repo = Repository(
        name="owner/fixture",
        image=image,
        install=[["python", "-m", "pip", "install", "--no-cache-dir", "six==1.17.0"]],
    )
    with DockerSandbox(repo, BASE, time.monotonic() + 120) as box:
        results = box.install()
        assert results[0].code == 0
        assert (
            box.command(["python", "-c", "import six; print(six.__version__)"]).output.strip()
            == "1.17.0"
        )
        with box.install_network():
            reject = box.command(
                [
                    "python",
                    "-c",
                    "import urllib.request; urllib.request.urlopen('https://example.com',timeout=5)",
                ],
                installation=True,
            )
            assert reject.code != 0
            bypass = box.command(
                [
                    "python",
                    "-c",
                    "import socket; socket.create_connection(('1.1.1.1',443),timeout=1)",
                ]
            )
            assert bypass.code != 0
        assert (
            box.command(
                [
                    "python",
                    "-c",
                    "import socket; socket.create_connection(('1.1.1.1',443),timeout=1)",
                ]
            ).code
            != 0
        )
