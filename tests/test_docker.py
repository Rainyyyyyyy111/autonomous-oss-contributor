import json
import os
import time

import pytest
from conftest import BASE, FIX, REGRESSION

from oss_contributor.config import Config, Repository
from oss_contributor.desktop import Desktop, Handoff
from oss_contributor.engine import Engine
from oss_contributor.models import Assessment, Repair
from oss_contributor.process import ContributorError
from oss_contributor.sandbox import DockerSandbox
from oss_contributor.store import Store

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(
        os.environ.get("OSS_TEST_DOCKER") != "1",
        reason="set OSS_TEST_DOCKER=1 to run Docker integration",
    ),
]


def test_real_docker_independent_red_green_verification(tmp_path):
    config = Config(state_dir=tmp_path / "state")
    store = Store(config)
    repo = Repository(name="owner/fixture", install=[])
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
    repo = Repository(name="owner/fixture", install=[])
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
    desktop = Desktop(Engine(config, store, github, codex, DockerSandbox))
    action = desktop.tick()
    assert action["action"] == "dispatch"
    desktop.attach(action["ticket"], "fixture-native-chat")
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


def test_installation_network_allows_pypi_and_denies_other_hosts():
    repo = Repository(
        name="owner/fixture",
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
