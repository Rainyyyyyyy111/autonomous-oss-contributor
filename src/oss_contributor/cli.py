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
from .environments import Environments
from .installation import atomic_write
from .learning import LearningMemory
from .notifications import NotificationWatcher
from .process import ContributorError, RateLimited, run_command
from .sandbox import DockerSandbox
from .store import Store

app = typer.Typer(no_args_is_help=True, help="Evidence-driven autonomous OSS contributions.")
desktop_app = typer.Typer(no_args_is_help=True, help="Persistent native-chat autopilot; never calls the standalone model adapter.")
app.add_typer(desktop_app, name="desktop")
notifications_app = typer.Typer(no_args_is_help=True, help="Scoped GitHub notification ingestion and triage; no publication or model calls.")
app.add_typer(notifications_app, name="notifications")
learning_app = typer.Typer(no_args_is_help=True, help="Inspect feedback evidence and preventive checklists.")
app.add_typer(learning_app, name="learning")
environments_app = typer.Typer(no_args_is_help=True, help="Prepare local runner images without target workspace reuse.")
app.add_typer(environments_app, name="environments")
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
def dashboard(config: ConfigOption = None, output: Annotated[Path | None, typer.Option("--output")] = None):
    """Export a scope-aware offline HTML status snapshot; no remote or model calls."""
    from .dashboard import render, snapshot

    store = Store(load(config))
    try:
        with store.lock():
            data = snapshot(Desktop(Engine(store.config, store)))
            target = output or store.home / "dashboard.html"
            if target.is_symlink():
                raise ContributorError("dashboard output cannot be a symbolic link")
            atomic_write(target, render(data).encode())
            target.chmod(0o600)
            emit({"dashboard": str(target.resolve()), "generated_at": data["generated_at"],
                  "notice": "Offline snapshot; rerun to refresh. No tasks were started."})
    finally:
        store.close()


@environments_app.command("prepare")
def prepare_environments(config: ConfigOption = None, build_runner: bool = typer.Option(False, "--build-runner")):
    """Inspect configured local images and probe isolation in empty disposable containers."""
    store = Store(load(config))
    try:
        with store.lock():
            if store.desktop_jobs():
                raise ContributorError("native chat active; do not change runner images during a repair")
            if not DockerSandbox.doctor()["ok"]:
                raise ContributorError("Docker unavailable; start Docker before environment preparation")
            if build_runner:
                DockerSandbox.build()
            manager = Environments(store)
            repositories = {repo.image: repo for repo in store.config.repositories}
            reports = [manager.prepare(repo, refresh=True) for repo in repositories.values()]
            emit({"environments": reports,
                  "notice": "Only runner images are prepared. Target dependencies install fresh per validation."})
            if any(not r["ok"] for r in reports):
                raise typer.Exit(1)
    finally:
        store.close()


@app.command()
def pause(config: ConfigOption = None):
    """Block writes and request native cancellation; retain reservations until confirmed stopped."""
    store = Store(load(config))
    try:
        store.pause("paused by user")
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


@learning_app.command("report")
def learning_report(config: ConfigOption = None):
    """Export local feedback records and hypotheses; no network or model calls."""
    store = Store(load(config))
    try:
        with store.lock():
            memory = LearningMemory(store)
            emit({**memory.export(), **{k: v for k, v in memory.report().items() if k != "records"}})
    finally:
        store.close()


@learning_app.command("refresh")
def learning_refresh(config: ConfigOption = None):
    """Backfill known allowlisted PRs, including closed PRs; no models or GitHub writes."""
    store = Store(load(config))
    try:
        with store.lock():
            desktop = Desktop(Engine(store.config, store))
            if store.meta("paused"):
                raise ContributorError("paused by user")
            if time.time() < float(store.meta("github_retry_after", "0")):
                raise ContributorError("GitHub server-requested backoff")
            targets = NotificationWatcher(desktop).targets()
            for task in targets.values():
                remote = desktop.engine.github.pr_state(task["repo"], task["data"]["pr"]["number"])
                # Learning only: no triage, task transitions, dispatch or marking events processed.
                desktop.engine.learning.observe(task, remote)
            emit({"inspected_prs": len(targets), **desktop.engine.learning.export()})
    except RateLimited as exc:
        store.set_meta("github_retry_after", str(time.time() + exc.seconds))
        raise
    finally:
        store.close()


def mutate_memory(config: ConfigOption, task_id: int, record_id: str, *, delete: bool):
    store = Store(load(config))
    try:
        with store.lock():
            emit(LearningMemory(store).mutate(task_id, record_id, delete=delete))
    finally:
        store.close()


@learning_app.command("invalidate")
def invalidate_memory(task_id: int, record_id: str, config: ConfigOption = None):
    """Operator-only: retire one scoped learning record; do not change task authorization."""
    mutate_memory(config, task_id, record_id, delete=False)


@learning_app.command("delete")
def delete_memory(task_id: int, record_id: str, config: ConfigOption = None):
    """Operator-only: remove learning payload/exports and retain a no-content tombstone/audit."""
    mutate_memory(config, task_id, record_id, delete=True)


@learning_app.command("audit")
def memory_audit(config: ConfigOption = None):
    """Inspect local scope-filtered memory mutation metadata, without source text."""
    store = Store(load(config))
    try:
        emit({"audit": LearningMemory(store).audit()})
    finally:
        store.close()


@notifications_app.command("poll")
def notification_poll(
    config: ConfigOption = None, quiet: bool = typer.Option(False, "--quiet"),
    desktop_alerts: bool = typer.Option(False, "--desktop-alerts"),
):
    """Read notifications once, persist triage and queue reviews for the native coordinator."""
    store = Store(load(config))
    try:
        try:
            with store.lock():
                watcher = NotificationWatcher(Desktop(Engine(store.config, store)))
                result = watcher.poll()
                if desktop_alerts and result["state"] not in {"disabled", "paused"}:
                    alert_error = watcher.desktop_alerts()
                    if alert_error:
                        result["alert_error"] = alert_error
        except ContributorError as exc:
            if str(exc) != "another worker is running":
                raise
            result = {"state": "busy", "decisions": []}
        if not quiet or result.get("decisions") or result.get("alert_error") or result["state"] in {"error", "backoff"}:
            emit(result)
    finally:
        store.close()


@notifications_app.command("watch")
def notification_watch(config: ConfigOption = None):
    """Run the cheap listener in the foreground; native chats remain owned by the App."""
    cfg = load(config)
    try:
        while True:
            store = Store(cfg)
            try:
                with store.lock():
                    result = NotificationWatcher(Desktop(Engine(cfg, store))).poll()
                    if result.get("decisions") or result["state"] == "error":
                        emit(result)
            except ContributorError as exc:
                if str(exc) != "another worker is running":
                    raise
            finally:
                store.close()
            time.sleep(cfg.notifications.poll_seconds)
    except KeyboardInterrupt:
        typer.echo("Notification listener stopped; queued events are preserved.")


@notifications_app.command("service")
def notification_service_command(
    config: ConfigOption = None,
    install: bool = typer.Option(False, "--install"),
    uninstall: bool = typer.Option(False, "--uninstall"),
):
    """Install/remove the macOS listener; this never starts native chats or changes PRs."""
    from .notification_service import notification_service

    if install == uninstall:
        raise typer.BadParameter("choose exactly one of --install or --uninstall")
    emit(notification_service(config or config_path(), uninstall))


@desktop_app.command("tick")
def desktop_tick(config: ConfigOption = None, monitor_only: bool = typer.Option(False, "--monitor-only")):
    """Select work automatically, resume a native lease, reconcile publication, and observe PRs."""
    native_call(config, "tick", monitor_only=monitor_only)


@desktop_app.command("status")
def desktop_status(config: ConfigOption = None):
    """Read local scope, leases, budgets and blockers without network or model calls."""
    cfg = load(config)
    store = Store(cfg)
    try:
        emit(Desktop(Engine(cfg, store)).status())
    finally:
        store.close()


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


@desktop_app.command("usage")
def desktop_usage(ticket: str, observation: Path, config: ConfigOption = None):
    """Coordinator only: record actually observed App cumulative usage, never worker claims."""
    native_call(config, "record_usage", ticket, observation)


@app.command("accounting")
def accounting_report(config: ConfigOption = None):
    """Read local model provenance/usage; missing telemetry and prices stay unknown."""
    store = Store(load(config))
    try:
        emit(Desktop(Engine(store.config, store)).accounting_report())
    finally:
        store.close()


@app.command("evaluate")
def evaluate_controller(output: Annotated[Path, typer.Option("--output")],
                        image: str = typer.Option("oss-contributor-runner:0.1", "--image"),
                        candidates: Annotated[Path | None, typer.Option("--candidates")] = None):
    """Evaluate frozen synthetic controller fixtures in local Docker; no live config/model/GitHub."""
    from .evaluation import read_evaluation_json, run_evaluation

    report = run_evaluation(image, read_evaluation_json(candidates) if candidates else None)
    if output.is_symlink():
        raise ContributorError("evaluation output cannot be linked")
    atomic_write(output, json.dumps(report, ensure_ascii=False, indent=2).encode())
    output.chmod(0o600)
    emit({"report": str(output.resolve()), "metrics": report["metrics"], "scope": report["scope"]})
    if report["metrics"]["matching_decisions"] != report["metrics"]["cases"]:
        raise typer.Exit(1)


@app.command("evaluation-compare")
def compare_evaluations(before: Path, after: Path):
    """Compare matching frozen-corpus reports without invoking models or Docker."""
    from .evaluation import compare_reports, read_evaluation_json

    emit(compare_reports(read_evaluation_json(before), read_evaluation_json(after)))


@desktop_app.command("stopped")
def desktop_stopped(ticket: str, config: ConfigOption = None,
                    thread_id: Annotated[str | None, typer.Option("--thread-id")] = None,
                    not_created: bool = typer.Option(False, "--not-created")):
    """Record a native wait's terminal result, or confirmed absence after creation reconciliation."""
    native_call(config, "confirm_stopped", ticket, thread_id, not_created)


@desktop_app.command("fail")
def desktop_fail(ticket: str, reason: str, config: ConfigOption = None, quota: bool = typer.Option(False, "--quota")):
    """Record a stopped/failed native chat; never silently retry or switch auth."""
    native_call(config, "fail", ticket, reason, quota=quota)


@desktop_app.command("ack")
def desktop_ack(ids: list[int], config: ConfigOption = None):
    """Acknowledge notifications after delivery; duplicates remain quiet across restarts."""
    native_call(config, "acknowledge", ids)


@desktop_app.command("scope")
def desktop_scope(config: ConfigOption = None,
                  monitor: bool = typer.Option(False, "--monitor"),
                  autopilot: bool = typer.Option(False, "--autopilot"),
                  task_ids: Annotated[list[int] | None, typer.Option("--task-id")] = None):
    """Persist full authorized autopilot or a fixed set of existing PR tasks."""
    if monitor == autopilot:
        raise typer.BadParameter("choose exactly one of --monitor or --autopilot")
    native_call(config, "bind_scope", monitor, task_ids)


@desktop_app.command("setup")
def desktop_setup(config: ConfigOption = None):
    """Print the one-time App startup instruction, not a shell daemon or fake desktop API."""
    cfg = load(config)
    store = Store(cfg)
    try:
        scope = Desktop(Engine(cfg, store)).scope()
    finally:
        store.close()
    emit({"config": str((config or config_path()).resolve()), "allowlisted_repositories": len(cfg.repositories),
          "scope": scope, "heartbeat_minutes": 10, "prompt": heartbeat_prompt(config or config_path(), scope),
          "required_app_tools": ["list_projects", "create_thread", "wait_threads", "list_threads", "automation_update"]})


@desktop_app.command("install-skill")
def desktop_install_skill(config: ConfigOption = None, directory: Annotated[Path | None, typer.Option("--directory")] = None,
                          upgrade: bool = typer.Option(False, "--upgrade")):
    """Install the bundled App coordinator skill, preserving any existing installation."""
    destination = directory or Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "skills"
    target = install_skill(destination, config, upgrade=upgrade)
    emit({"skill": str(target), "start": "Use $oss-contributor to start automatic discovery, repair and publication inside my configured allowlist. Create a new native chat for each repair/revision."})


def main():
    try:
        app()
    except (ContributorError, OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
