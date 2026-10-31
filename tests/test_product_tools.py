import json
import time
from contextlib import contextmanager

import pytest
from test_engine import run_issue
from typer.testing import CliRunner

from oss_contributor.cli import app
from oss_contributor.dashboard import ci_summary, render, snapshot
from oss_contributor.desktop import Desktop
from oss_contributor.environments import Environments
from oss_contributor.models import CommandResult, Issue
from oss_contributor.process import ContributorError


def test_dashboard_is_offline_scope_aware_and_escapes_remote_text(rig):
    desktop = Desktop(rig[4])
    task = run_issue(rig)
    rig[1].update(task["id"], reason='<script>alert("steal")</script>')
    foreign = rig[1].create_task(Issue(repo="outside/repo", number=3, title="hidden"))
    desktop.bind_scope(True, [task["id"]])
    before_usage = rig[1].usage()
    before_tasks = rig[1].tasks()
    before_events = rig[1].db.execute("SELECT count(*) FROM events").fetchone()[0]

    def forbidden(*args, **kwargs):
        raise AssertionError("offline dashboard cannot call GitHub/models")

    rig[2].issue = rig[2].pr_state = rig[2].discover = forbidden
    rig[3].ask = forbidden
    data = snapshot(desktop)
    html = render(data)
    assert [t["id"] for t in data["tasks"]] == [task["id"]]
    assert foreign not in [t["id"] for t in data["queue"]]
    assert '<script>alert' not in html and "&lt;script&gt;" in html
    assert "Content-Security-Policy" in html and "default-src 'none'" in html
    assert "outside/repo" not in html
    assert rig[1].usage() == before_usage and rig[1].tasks() == before_tasks
    assert rig[1].db.execute("SELECT count(*) FROM events").fetchone()[0] == before_events


def test_dashboard_cli_outputs_private_snapshot_without_dispatch(rig, tmp_path, monkeypatch):
    from oss_contributor import cli

    monkeypatch.setattr(cli, "load", lambda path: rig[0])
    monkeypatch.setattr(cli, "Engine", lambda cfg, store: rig[4])
    path = tmp_path / "overview.html"
    result = CliRunner().invoke(app, ["dashboard", "--output", str(path)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["dashboard"] == str(path)
    assert path.stat().st_mode & 0o777 == 0o600
    assert not rig[1].desktop_jobs() and not rig[3].calls
    path.unlink()
    target = tmp_path / "private.txt"
    target.write_text("do not touch")
    path.symlink_to(target)
    result = CliRunner().invoke(app, ["dashboard", "--output", str(path)])
    assert result.exit_code != 0 and target.read_text() == "do not touch"


def test_dashboard_does_not_confuse_missing_old_or_waiting_ci_with_success():
    task = {"data": {"pr": {"head_sha": "a" * 40}}}
    assert ci_summary(task) == "not observed"
    observation = {"head_sha": "a" * 40, "ci": []}
    task["data"]["desktop_observation"] = observation
    assert ci_summary(task) == "no remote checks observed"
    observation["waiting"] = "CI awaiting maintainer authorization"
    assert ci_summary(task) == observation["waiting"]
    observation["head_sha"] = "b" * 40
    assert "different head" in ci_summary(task)


def test_environment_preparation_pins_image_and_reuses_only_probe_metadata(rig, monkeypatch):
    from oss_contributor import environments

    image = "sha256:" + "a" * 64
    commands, probes = [], []

    def command(argv, **kwargs):
        commands.append(argv)
        return CommandResult(argv=argv, code=0, output="Docker client warning\n" + image + "\n")

    class Box:
        def assert_isolated(self):
            return None

    @contextmanager
    def sandbox(repo, files, deadline):
        assert files == {} and repo.install == [] and repo.image == image
        assert deadline > time.monotonic()
        probes.append(repo)
        yield Box()

    monkeypatch.setattr(environments, "run_command", command)
    monkeypatch.setattr(environments, "DockerSandbox", sandbox)
    manager = Environments(rig[1])
    repo = rig[0].repositories[0]
    assert manager.prepare(repo)["ok"]
    assert manager.prepare(repo)["ok"]
    assert len(probes) == 1 and len(commands) == 2
    image = "sha256:" + "b" * 64
    assert manager.prepare(repo)["ok"]
    assert len(probes) == 2
    assert all(argv[:3] == ["docker", "image", "inspect"] for argv in commands)
    assert rig[1].usage()["model_seconds"] == 0 and not rig[1].tasks()
    assert manager.reports()[0]["image_id"] == image


@pytest.mark.parametrize("mode", ["missing", "isolation"])
def test_environment_failure_cannot_dispatch_or_spend_model_budget(rig, monkeypatch, mode):
    from oss_contributor import environments
    from oss_contributor.sandbox import DockerSandbox

    monkeypatch.setattr(environments, "run_command", lambda argv, **kwargs: CommandResult(
        argv=argv, code=1 if mode == "missing" else 0,
        output="missing" if mode == "missing" else "sha256:" + "a" * 64,
    ))

    def rejected(*args, **kwargs):
        raise ContributorError("container isolation check failed")

    monkeypatch.setattr(environments, "DockerSandbox", rejected)
    monkeypatch.setattr(DockerSandbox, "doctor", staticmethod(lambda: {"ok": True}))
    rig[4].cleanup_needed = True
    task_id = rig[1].create_task(rig[2].issue_value)
    assert Desktop(rig[4]).dispatch(rig[1].get(task_id)) is None
    assert "runner environment blocked" in rig[1].meta("paused")
    assert not rig[1].desktop_jobs() and rig[1].usage()["attempts"] == 0
    assert rig[1].usage()["model_seconds"] == 0 and not rig[3].calls
    assert not Environments(rig[1]).reports()[0]["ok"]


def test_environments_cli_refuses_active_job(rig, monkeypatch):
    from oss_contributor import cli

    action = Desktop(rig[4]).tick()
    assert action["action"] == "dispatch"
    monkeypatch.setattr(cli, "load", lambda path: rig[0])
    result = CliRunner().invoke(app, ["environments", "prepare", "--build-runner"])
    assert result.exit_code != 0
    assert rig[1].desktop_jobs()[0]["ticket"] == action["ticket"]
