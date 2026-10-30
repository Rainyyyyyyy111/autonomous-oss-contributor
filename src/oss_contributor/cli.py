from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Annotated, Any

import typer

from .codex import Codex
from .config import TEMPLATE, Config, default_home, load_config
from .desktop import Desktop, heartbeat_prompt, install_skill
from .engine import Engine
from .process import ContributorError, run_command
from .sandbox import DockerSandbox
from .store import Store

app = typer.Typer(no_args_is_help=True, help="Evidence-driven autonomous OSS contributions.")
desktop_app = typer.Typer(no_args_is_help=True, help="Persistent native-chat autopilot; never calls the standalone model adapter.")
app.add_typer(desktop_app, name="desktop")
ConfigOption = Annotated[Path | None, typer.Option("--config", "-c")]


def config_path() -> Path:
    return Path.home() / ".config/oss-contributor/config.toml"


def load(path: Path | None) -> Config:
    chosen = path or config_path()
    if not chosen.is_file():
        raise ContributorError(f"configuration missing: {chosen}; run init first")
    return load_config(chosen)


def emit(value):
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2, default=str))


@app.command()
def init(config: ConfigOption = None):
    """Create an empty allowlist configuration; never overwrite existing configuration."""
    path = config or config_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        with path.open("x") as stream:
            stream.write(TEMPLATE)
    except FileExistsError:
        raise typer.BadParameter(f"configuration already exists: {path}") from None
    path.chmod(0o600)
    emit({"config": str(path), "allowlisted_repositories": 0, "state_dir": str(default_home())})


@app.command()
def doctor(
    config: ConfigOption = None,
    probe_codex: bool = typer.Option(False, "--probe-codex"),
    build_runner: bool = typer.Option(False, "--build-runner"),
    desktop: bool = typer.Option(False, "--desktop", help="Check native App path without requiring a standalone Codex CLI login."),
):
    """Check prerequisites. Optional protocol probe spends no model tokens."""
    cfg = load(config)
    checks: dict[str, dict[str, Any]] = {}
    for binary in (("gh", "docker") if desktop else ("gh", "codex", "docker")):
        checks[binary] = {"ok": bool(shutil.which(binary))}
    if checks["gh"]["ok"]:
        result = run_command(["gh", "auth", "status"], timeout=15)
        checks["github_auth"] = {
            "ok": result.code == 0,
            "detail": "logged in" if not result.code else "run gh auth login",
        }
    if checks["docker"]["ok"]:
        checks["docker_daemon"] = DockerSandbox.doctor()
        if checks["docker_daemon"]["ok"]:
            if build_runner:
                DockerSandbox.build()
            image = run_command(
                ["docker", "image", "inspect", "oss-contributor-runner:0.1"], timeout=10
            )
            checks["runner_image"] = {
                "ok": image.code == 0,
                "detail": "available" if not image.code else "run doctor --build-runner",
            }
    if not desktop and checks["codex"]["ok"]:
        try:
            client = Codex(cfg)
            client.version_check()
            result = run_command(["codex", "login", "status"], timeout=10)
            checks["codex_auth"] = {"ok": result.code == 0, "detail": result.output.strip()}
            if probe_codex:
                checks["codex_protocol"] = client.probe()
        except (ContributorError, OSError) as exc:
            checks["codex_protocol"] = {"ok": False, "detail": str(exc)}
    checks["allowlist"] = {
        "ok": True,
        "count": len(cfg.repositories),
        "detail": "empty means no contributions will be published",
    }
    emit(checks)
    if any(not item["ok"] for item in checks.values()):
        raise typer.Exit(1)


@app.command()
def discover(config: ConfigOption = None):
    """Read GitHub candidates and enqueue allowlisted issues; never publish."""
    cfg = load(config)
    store = Store(cfg)
    try:
        with store.lock():
            emit(Engine(cfg, store).discover())
    finally:
        store.close()


@app.command()
def run(
    config: ConfigOption = None,
    once: bool = typer.Option(False, "--once"),
    daemon: bool = typer.Option(False, "--daemon"),
):
    """Process eligible work once or poll until interrupted."""
    if once == daemon:
        raise typer.BadParameter("choose exactly one of --once or --daemon")
    cfg, store = load(config), None
    try:
        store = Store(cfg)
        with store.lock():
            engine = Engine(cfg, store)
            while True:
                try:
                    engine.once()
                except (ContributorError, OSError, ValueError) as exc:
                    store.event(None, "worker_error", {"reason": str(exc)})
                    if once:
                        raise
                    typer.echo(f"Worker error: {exc}", err=True)
                if once:
                    emit(
                        {
                            "usage": store.usage(),
                            "paused": store.meta("paused"),
                            "tasks": [
                                {k: t[k] for k in ("id", "repo", "issue", "status")}
                                for t in store.tasks()
                            ],
                        }
                    )
                    break
                time.sleep(5)
    except KeyboardInterrupt:
        typer.echo("Stopped. Incomplete work will be reconciled on the next run.")
    finally:
        if store:
            store.close()


@app.command()
def status(config: ConfigOption = None):
    store = Store(load(config))
    try:
        emit(
            {
                "paused": store.meta("paused"),
                "usage": store.usage(),
                "tasks": [
                    {
                        "id": t["id"],
                        "repo": t["repo"],
                        "issue": t["issue"],
                        "status": t["status"],
                        "reason": t["data"].get("reason", ""),
                        "pr": t["data"].get("pr"),
                    }
                    for t in store.tasks()
                ],
            }
        )
    finally:
        store.close()


@app.command("inspect")
def inspect_task(task_id: int, config: ConfigOption = None):
    store = Store(load(config))
    try:
        emit(store.get(task_id))
    finally:
        store.close()


@app.command()
def pause(config: ConfigOption = None):
    """Stop new model calls and publication; the current bounded tool call may finish."""
    store = Store(load(config))
    try:
        store.set_meta("paused", "paused by user")
        emit({"paused": True})
    finally:
        store.close()


@app.command()
def resume(config: ConfigOption = None):
    """Resume the worker without resetting budgets or failed/revision-limited tasks."""
    store = Store(load(config))
    try:
        store.set_meta("paused", "")
        emit({"paused": False})
    finally:
        store.close()


def native_call(config: Path | None, method: str, *args, **kwargs):
    cfg = load(config)
    store = Store(cfg)
    try:
        with store.lock():
            emit(getattr(Desktop(Engine(cfg, store)), method)(*args, **kwargs))
    finally:
        store.close()


@desktop_app.command("tick")
def desktop_tick(config: ConfigOption = None, monitor_only: bool = typer.Option(False, "--monitor-only")):
    """Select work automatically, resume a native lease, reconcile publication, and observe PRs."""
    native_call(config, "tick", monitor_only=monitor_only)


@desktop_app.command("attach")
def desktop_attach(ticket: str, thread_id: str, config: ConfigOption = None):
    """Bind a persisted dispatch to the native chat returned by the App."""
    native_call(config, "attach", ticket, thread_id)


@desktop_app.command("finish")
def desktop_finish(ticket: str, handoff: Path, config: ConfigOption = None):
    """Independently verify a native handoff and automatically publish if all gates pass."""
    native_call(config, "finish", ticket, handoff)


@desktop_app.command("checkpoint")
def desktop_checkpoint(ticket: str, cursor: str, config: ConfigOption = None):
    """Persist the latest native wait cursor for compact restart recovery."""
    native_call(config, "checkpoint", ticket, cursor)


@desktop_app.command("fail")
def desktop_fail(ticket: str, reason: str, config: ConfigOption = None, quota: bool = typer.Option(False, "--quota")):
    """Record a stopped/failed native chat; never silently retry or switch auth."""
    native_call(config, "fail", ticket, reason, quota=quota)


@desktop_app.command("ack")
def desktop_ack(ids: list[int], config: ConfigOption = None):
    """Acknowledge notifications after delivery; duplicates remain quiet across restarts."""
    store = Store(load(config))
    try:
        with store.lock():
            store.acknowledge(ids)
            emit({"acknowledged": ids})
    finally:
        store.close()


@desktop_app.command("setup")
def desktop_setup(config: ConfigOption = None):
    """Print the one-time App startup instruction, not a shell daemon or fake desktop API."""
    cfg = load(config)
    emit({"config": str((config or config_path()).resolve()), "allowlisted_repositories": len(cfg.repositories),
          "heartbeat_minutes": 10, "prompt": heartbeat_prompt(config or config_path()),
          "required_app_tools": ["list_projects", "create_thread", "wait_threads", "list_threads", "automation_update"]})


@desktop_app.command("install-skill")
def desktop_install_skill(config: ConfigOption = None, directory: Annotated[Path | None, typer.Option("--directory")] = None):
    """Install the bundled App coordinator skill, preserving any existing installation."""
    destination = directory or Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "skills"
    target = install_skill(destination, config or config_path())
    emit({"skill": str(target), "start": "Use $oss-contributor to start automatic discovery, repair and publication inside my configured allowlist. Create a new native chat for each repair/revision."})


def main():
    try:
        app()
    except (ContributorError, OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
