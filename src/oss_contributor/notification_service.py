"""Optional macOS scheduling for the read-only listener, independent of model heartbeats."""

from __future__ import annotations

import hashlib
import os
import platform
import plistlib
import shutil
import sys
from pathlib import Path

from .config import load_config
from .installation import atomic_write
from .process import ContributorError, run_command


def service_spec(config_path: Path, installing: bool = True) -> tuple[Path, dict]:
    path = config_path.expanduser().resolve(strict=True)
    config = load_config(path)
    if installing and not config.notifications.enabled:
        raise ContributorError("enable notifications in the configuration before installing the listener")
    gh = shutil.which("gh")
    if installing and not gh:
        raise ContributorError("GitHub CLI is required for the notification listener")
    home = config.state_dir.expanduser().resolve()
    key = hashlib.sha256(str(home).encode()).hexdigest()[:16]
    label = "io.oss-contributor.notifications." + key
    arguments = [sys.executable, "-m", "oss_contributor.cli", "notifications",
                 "poll", "--quiet", "--config", str(path)]
    if config.notifications.desktop_alerts:
        arguments.append("--desktop-alerts")
    directories = dict.fromkeys([str(Path(sys.executable).parent), str(Path(gh or "/usr/bin/gh").parent),
                                 "/usr/bin", "/bin", "/usr/sbin", "/sbin"])
    return Path.home() / "Library/LaunchAgents" / (label + ".plist"), {
        "Label": label,
        "ProgramArguments": arguments,
        "RunAtLoad": True,
        "StartInterval": config.notifications.poll_seconds,
        "EnvironmentVariables": {"PATH": ":".join(directories)},
        "StandardOutPath": str(home / "notification-listener.log"),
        "StandardErrorPath": str(home / "notification-listener-error.log"),
        "ProcessType": "Background",
    }


def notification_service(config_path: Path, uninstall: bool = False) -> dict:
    if platform.system() != "Darwin":
        raise ContributorError("service installation requires macOS; use notifications watch on other systems")
    path, spec = service_spec(config_path, installing=not uninstall)
    if path.is_symlink():
        raise ContributorError("refusing a linked notification service file")
    previous = path.read_bytes() if path.exists() else None
    if previous:
        existing = plistlib.loads(previous)
        args = existing.get("ProgramArguments", [])
        if existing.get("Label") != spec["Label"] or args[1:5] != [
            "-m", "oss_contributor.cli", "notifications", "poll",
        ]:
            raise ContributorError("existing service file is not managed by OSS Contributor")
    domain = f"gui/{os.getuid()}"
    loaded = run_command(["launchctl", "print", domain + "/" + spec["Label"]], timeout=10).code == 0
    if loaded:
        stopped = run_command(["launchctl", "bootout", domain + "/" + spec["Label"]], timeout=10)
        if stopped.code:
            raise ContributorError("unable to stop existing notification listener: " + stopped.output[-500:])
    if uninstall:
        path.unlink(missing_ok=True)
        return {"installed": False, "label": spec["Label"]}
    Path(spec["StandardOutPath"]).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write(path, plistlib.dumps(spec))
    started = run_command(["launchctl", "bootstrap", domain, str(path)], timeout=10)
    if started.code:
        if previous:
            atomic_write(path, previous)
            if loaded:
                run_command(["launchctl", "bootstrap", domain, str(path)], timeout=10)
        else:
            path.unlink(missing_ok=True)
        raise ContributorError("unable to start notification listener: " + started.output[-500:])
    return {"installed": True, "label": spec["Label"], "path": str(path),
            "poll_seconds": spec["StartInterval"], "mode": "observe_and_queue"}
