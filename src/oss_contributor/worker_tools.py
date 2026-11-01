"""Ticket-bound native worker toolbox; host permissions remain an App boundary."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .config import Config, Repository
from .errors import (
    Cancelled,
    ErrorInfo,
    FatalOperationError,
    InvalidArguments,
    PermissionDenied,
    classify_error,
)
from .github import GitHub
from .process import ContributorError
from .retries import RetryPolicy, retry_read
from .safety import check_regression
from .sandbox import DockerSandbox
from .store import Store
from .tool_guard import ToolGuard, ToolPolicy, ToolState, fingerprint


class WorkerTools:
    """Use install(), tool(name, arguments), and snapshot() inside a with block.

    Repository bytes come from the pinned host adapter, execution from DockerSandbox.
    Dependency injection exists for trusted tests, never in a worker task bundle.
    """

    def __init__(self, job_path: Path | str, *, github=None, sandbox_factory=None):
        path = Path(job_path)
        if path.is_symlink() or path.name != "job.json" or path.parent.parent.name != "jobs":
            raise ContributorError("worker toolbox requires the assigned job.json path")
        self.ticket = path.parent.name
        self.store = Store(Config(state_dir=path.parent.parent.parent))
        self.github = github if github is not None else GitHub()
        self.factory = sandbox_factory or DockerSandbox
        self.box: Any = None
        self.workspace = fingerprint({})

    def _job(self) -> dict:
        job = self.store.desktop_job(self.ticket)
        task = self.store.get(job["task"])
        scope = json.loads(self.store.meta("desktop_scope", '{"mode":"autopilot"}'))
        identity = {"task": task["id"], "repo": task["repo"], "issue": task["issue"],
                    "pr": (task["data"].get("pr") or {}).get("number")}
        if scope["mode"] == "monitor" and identity not in scope["targets"]:
            raise PermissionDenied("worker tool ticket is outside the persisted monitor scope")
        if (self.store.meta("paused") or job["state"] != "attached"
            or job["data"].get("thread_stopped") or time.time() >= min(job["expires"], job["data"].get("worker_expires", job["expires"]))
            or task["status"] != "desktop_running"):
            raise Cancelled("worker tool ticket is not running")
        if "tool_policy" not in job["data"]:
            raise ContributorError("legacy job has no tool monitoring policy")
        return job

    def _guard(self, job: dict) -> ToolGuard:
        def save(state):
            current = self.store.desktop_job(self.ticket)
            current["data"]["tool_activity"] = state.model_dump()
            if state.blocked:
                if current["state"] not in {"complete", "failed"}:
                    current["state"] = "cancelling"
                current["data"]["stop_reason"] = state.blocked
                self.store.notify(f"tool-limit:{self.ticket}", kind="needs_attention",
                                  task=current["task"], reason=state.blocked)
            self.store.save_desktop_job(current)

        return ToolGuard(ToolPolicy.model_validate(job["data"]["tool_policy"]),
                         ToolState.model_validate(job["data"].get("tool_activity", {})), save)

    def _run(self, name: str, arguments, operation):
        job = self._job()
        policy = RetryPolicy.model_validate(job["data"].get(
            "retry_policy", {"max_attempts": 1, "max_retries": 0},
        ))

        def attempt(end):
            previous_deadline = self.box.deadline if self.box is not None else None
            if previous_deadline is not None and end is not None:
                self.box.deadline = min(previous_deadline, end)
            try:
                return self._run_once(name, arguments, operation)
            finally:
                if previous_deadline is not None:
                    self.box.deadline = previous_deadline

        try:
            return retry_read(attempt, safe=name in {"repo_list", "repo_read", "repo_search", "repo_snapshot"},
                              policy=policy, reserve=lambda: self._reserve_retry(policy),
                              deadline=time.monotonic() + max(0, job["expires"] - time.time()),
                              check=self._job, on_error=self._remember_error)
        except FatalOperationError as exc:
            with self.store.lock():
                current = self.store.desktop_job(self.ticket)
                if current["state"] not in {"complete", "failed"}:
                    current["state"] = "cancelling"
                    current["data"]["stop_reason"] = str(exc)
                current["data"]["last_error"] = classify_error(exc).model_dump(mode="json")
                self.store.save_desktop_job(current)
                self.store.notify(f"worker-error:{self.ticket}", kind="needs_attention",
                                  task=current["task"], error=classify_error(exc).model_dump(mode="json"))
            raise

    def _remember_error(self, info: ErrorInfo):
        with self.store.lock():
            self._guard(self.store.desktop_job(self.ticket)).record_error(info)
            if info.action == "pause":
                self.store.pause("worker authentication or quota blocked")
            if info.retry_after_seconds is not None:
                self.store.set_meta("github_retry_after", str(time.time() + info.retry_after_seconds))

    def _reserve_retry(self, policy: RetryPolicy):
        with self.store.lock():
            return self._guard(self._job()).reserve_retry(policy)

    def _run_once(self, name: str, arguments, operation):
        with self.store.lock():
            self._guard(self._job()).begin(name, arguments, self.workspace)
        try:
            result = operation()
        except (ContributorError, ValueError, TypeError, OSError):
            self._finish(name, None, False)
            raise
        self._finish(name, result, True)
        return result

    def _finish(self, name: str, result, success: bool):
        # Read-only tools cannot change the workspace; mutating calls get a real snapshot.
        if self.box is not None and name in {
            "workspace_open", "repo_apply", "regression_write", "sandbox_exec", "repo_install",
            "repo_restore_prior",
        }:
            try:
                self.workspace = fingerprint(self.box.snapshot())
            except (ContributorError, ValueError, TypeError, OSError):
                with self.store.lock():
                    self._guard(self.store.desktop_job(self.ticket)).stop(
                        "tool guard: workspace observation unavailable"
                    )
        with self.store.lock():
            job = self.store.desktop_job(self.ticket)
            guard = self._guard(job)
            if guard.state.blocked:
                guard.stop(guard.state.blocked)
            guard.finish(name, result, success, self.workspace)

    def __enter__(self):
        try:
            def open_workspace():
                job = self._job()
                repo = Repository.model_validate(job["data"]["repository"])
                deadline = time.monotonic() + max(0, min(job["expires"], job["data"].get("worker_expires", job["expires"])) - time.time())
                if isinstance(self.github, GitHub):
                    self.github.retry_policy = RetryPolicy.model_validate(job["data"].get(
                        "retry_policy", {"max_attempts": 1, "max_retries": 0},
                    ))
                    self.github.reserve_retry = lambda: self._reserve_retry(self.github.retry_policy)
                    self.github.before_read = self._job
                    self.github.on_error = self._remember_error
                    with self.github.bounded(deadline):
                        files = self.github.snapshot(repo.name, job["data"]["base_sha"])
                else:
                    files = self.github.snapshot(repo.name, job["data"]["base_sha"])
                self.box = self.factory(repo, files, deadline)
                self.box.__enter__()
                return None

            self._run("workspace_open", {}, open_workspace)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        try:
            if self.box is not None:
                self.box.__exit__(*args)
        finally:
            self.store.close()

    def install(self):
        return self._run("repo_install", {}, lambda: self.box.install())

    def snapshot(self):
        return self.tool("repo_snapshot", {})

    def tool(self, name: str, arguments: dict):
        def execute():
            if not isinstance(arguments, dict):
                raise InvalidArguments("tool arguments must be an object")
            if name == "repo_snapshot" and arguments == {}:
                return self.box.snapshot()
            if name == "repo_restore_prior" and arguments == {}:
                return self.box.apply(self._job()["data"]["prior_changes"])
            if name == "regression_write" and set(arguments) == {"content"}:
                job = self._job()
                content = arguments["content"]
                check_regression(content)
                path = job["data"]["regression_path"]
                previous = self.box.snapshot().get(path)
                prior = job["data"]["prior_changes"].get(path)
                if (previous is not None and previous != content) or (prior and prior != content):
                    raise PermissionDenied("original regression must remain unchanged")
                return self.box.apply({path: content})
            if name in {"repo_snapshot", "repo_restore_prior", "regression_write"}:
                raise InvalidArguments("invalid arguments for worker tool")
            if name not in {"repo_list", "repo_read", "repo_search", "repo_apply", "sandbox_exec"}:
                raise PermissionDenied("tool not in native worker allowlist")
            return self.box.tool(name, arguments, writable=True)

        return self._run(name, arguments, execute)
