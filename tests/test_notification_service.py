from __future__ import annotations

import plistlib
from contextlib import contextmanager
from pathlib import Path

import pytest
from typer.testing import CliRunner

import oss_contributor.notification_service as service_module
from oss_contributor.cli import app
from oss_contributor.models import CommandResult
from oss_contributor.process import ContributorError
from oss_contributor.store import Store


def setup_service(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(service_module.shutil, "which", lambda name: "/opt/homebrew/bin/gh")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    config = tmp_path / 'config with spaces.toml'
    config.write_text('state_dir = "' + str(tmp_path / "state") + '"\n')
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return CommandResult(argv=argv, code=1 if argv[1] == "print" else 0)

    monkeypatch.setattr(service_module, "run_command", run)
    return config, calls


def test_service_installs_only_scoped_read_only_poll_command(tmp_path, monkeypatch):
    config, calls = setup_service(tmp_path, monkeypatch)
    result = service_module.notification_service(config)
    path = Path(result["path"])
    data = plistlib.loads(path.read_bytes())
    assert result["mode"] == "observe_and_queue" and result["poll_seconds"] == 60
    args = data["ProgramArguments"]
    assert args[1:5] == ["-m", "oss_contributor.cli", "notifications", "poll"]
    assert str(config) in args
    assert "--desktop-alerts" in args
    assert "tick" not in args and "run" not in args
    assert "/opt/homebrew/bin" in data["EnvironmentVariables"]["PATH"]
    assert [c[1] for c in calls] == ["print", "bootstrap"]
    service_module.notification_service(config, uninstall=True)
    assert not path.exists()


def test_service_failed_bootstrap_restores_previous_definition(tmp_path, monkeypatch):
    config, calls = setup_service(tmp_path, monkeypatch)
    result = service_module.notification_service(config)
    path = Path(result["path"])
    previous = path.read_bytes()

    def fail(argv, **kwargs):
        return CommandResult(argv=argv, code=1)

    monkeypatch.setattr(service_module, "run_command", fail)
    with pytest.raises(ContributorError, match="unable to start"):
        service_module.notification_service(config)
    assert path.read_bytes() == previous


def test_service_does_not_overwrite_unmanaged_plist(tmp_path, monkeypatch):
    config, calls = setup_service(tmp_path, monkeypatch)
    path, spec = service_module.service_spec(config)
    path.parent.mkdir(parents=True)
    path.write_bytes(plistlib.dumps({"Label": spec["Label"], "ProgramArguments": ["other-tool"]}))
    with pytest.raises(ContributorError, match="not managed"):
        service_module.notification_service(config)
    assert not calls


def test_service_can_be_removed_after_notifications_are_disabled(tmp_path, monkeypatch):
    config, calls = setup_service(tmp_path, monkeypatch)
    result = service_module.notification_service(config)
    config.write_text(config.read_text() + "\n[notifications]\nenabled = false\n")
    with pytest.raises(ContributorError, match="enable notifications"):
        service_module.notification_service(config)
    assert not service_module.notification_service(config, uninstall=True)["installed"]
    assert not Path(result["path"]).exists()


def test_non_macos_service_provides_foreground_alternative(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module.platform, "system", lambda: "Linux")
    with pytest.raises(ContributorError, match="notifications watch"):
        service_module.notification_service(tmp_path / "unused.toml")


def test_scheduled_poll_treats_controller_lock_contention_as_quiet_retry(tmp_path, monkeypatch):
    config = tmp_path / "config.toml"
    config.write_text('state_dir = "' + str(tmp_path / "state") + '"\n')

    @contextmanager
    def busy(self):
        raise ContributorError("another worker is running")
        yield

    monkeypatch.setattr(Store, "lock", busy)
    result = CliRunner().invoke(app, ["notifications", "poll", "--quiet", "--config", str(config)])
    assert result.exit_code == 0 and result.output == ""
    result = CliRunner().invoke(app, ["notifications", "poll", "--config", str(config)])
    assert result.exit_code == 0 and '"state": "busy"' in result.output
