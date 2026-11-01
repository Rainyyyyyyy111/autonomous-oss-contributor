"""Bounded tool activity. Progress is an observation heuristic, never test evidence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from pydantic import Field

from .errors import ContributorError, ErrorInfo, ErrorKind, FatalOperationError, classify_error
from .models import StrictModel
from .retries import RetryPolicy


class ExecutionLimitExceeded(FatalOperationError):
    """Fatal: must escape tool-error responses so the model cannot retry it."""

    kind = ErrorKind.LIMIT
    action = "stop"


class ToolPolicy(StrictModel):
    max_steps: int = Field(120, ge=1, le=1000)
    max_repeated_calls: int = Field(4, ge=1, le=100)
    max_no_progress: int = Field(20, ge=1, le=1000)


class ToolState(StrictModel):
    retries_used: int = Field(0, ge=0)
    last_error: ErrorInfo | None = None
    steps: int = Field(0, ge=0)
    no_progress: int = Field(0, ge=0)
    calls: dict[str, int] = Field(default_factory=dict)
    observations: list[str] = Field(default_factory=list)
    workspaces: list[str] = Field(default_factory=list)
    pending: bool = False
    blocked: str = ""


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"),
    ).encode()).hexdigest()


class ToolGuard:
    def __init__(self, policy: ToolPolicy, state: ToolState | None = None, save=None):
        self.policy = policy
        self.state = state or ToolState()
        self.save = save or (lambda state: None)

    def stop(self, reason: str):
        self.state.blocked = self.state.blocked or reason
        self.save(self.state)
        raise ExecutionLimitExceeded(self.state.blocked)

    def record_error(self, info: ErrorInfo):
        self.state.last_error = info
        self.save(self.state)

    def reserve_retry(self, policy: RetryPolicy) -> bool:
        if self.state.retries_used >= policy.max_retries:
            return False
        self.state.retries_used += 1
        self.save(self.state)
        return True

    def begin(self, name: str, arguments: Any, workspace: str):
        state = self.state
        if state.blocked:
            self.stop(state.blocked)
        if state.pending:
            self.stop("tool guard: incomplete previous call requires attention")
        if state.steps >= self.policy.max_steps:
            self.stop("tool guard: total step limit reached")
        key = fingerprint([name, arguments, workspace])
        if state.calls.get(key, 0) >= self.policy.max_repeated_calls:
            self.stop("tool guard: repeated call limit reached")
        if workspace not in state.workspaces:
            state.workspaces.append(workspace)
        state.calls[key] = state.calls.get(key, 0) + 1
        state.steps += 1
        state.pending = True
        self.save(state)  # Reserve before the operation; a crash cannot refund a step.

    def finish(self, name: str, result: Any, success: bool, workspace: str):
        state = self.state
        # Volatile command duration/argv and changing error messages are not progress.
        evidence = result
        if name == "sandbox_exec" and isinstance(result, dict):
            success = success and result.get("code") == 0
            if not success:
                state.last_error = ErrorInfo(kind=ErrorKind.COMMAND, action="inspect_result")
            evidence = result.get("output", "")
        novel_workspace = workspace not in state.workspaces
        if novel_workspace:
            state.workspaces.append(workspace)
        progress = success and novel_workspace
        if name in {"repo_apply", "regression_write", "repo_install", "workspace_open", "repo_restore_prior"}:
            evidence = None  # Only actual workspace changes count for these operations.
        if success and evidence not in (None, "", [], {}):
            observed = fingerprint([name, evidence])
            if observed not in state.observations:
                state.observations.append(observed)
                progress = True
        state.no_progress = 0 if progress else state.no_progress + 1
        state.pending = False
        if state.no_progress >= self.policy.max_no_progress:
            self.stop("tool guard: no-progress limit reached")
        self.save(state)

    def run(self, name: str, arguments: Any, operation: Callable, snapshot: Callable):
        def workspace():
            try:
                return fingerprint(snapshot())
            except (ContributorError, ValueError, TypeError, OSError):
                self.stop("tool guard: workspace observation unavailable")

        before = workspace()
        self.begin(name, arguments, before)

        def after():
            return before if name in {"repo_list", "repo_read", "repo_search", "repo_snapshot"} else workspace()
        try:
            result = operation()
        except (ContributorError, ValueError, TypeError, OSError) as exc:
            self.record_error(classify_error(exc))
            self.finish(name, None, False, after())
            raise
        self.finish(name, result, True, after())
        return result
