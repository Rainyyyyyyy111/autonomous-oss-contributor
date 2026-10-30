from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CommandResult(StrictModel):
    argv: list[str]
    code: int
    output: str = ""
    seconds: float = 0


class Issue(StrictModel):
    repo: str
    number: int
    title: str
    body: str = ""
    url: str = ""
    state: str = "open"
    labels: list[str] = []
    comments: list[dict[str, Any]] = []
    conflicts: list[str] = []
    policy: str = ""
    pr_template: str = ""


class Assessment(StrictModel):
    suitable: bool
    reason: str
    evidence: list[str]
    reproduction: str
    expected_scope: list[str]
    validation: str
    root_cause: str = ""


class Regression(StrictModel):
    content: str
    explanation: str


class Repair(StrictModel):
    summary: str
    root_cause: str
    evidence: list[str]


class ValidationReport(StrictModel):
    base_sha: str
    passed: bool = False
    commands: list[CommandResult] = []
    failure: str = ""
    files: list[str] = []
    added: int = 0
    removed: int = 0


class ReviewEvent(StrictModel):
    id: str
    body: str
    kind: Literal["review", "ci"] = "review"


class PRState(StrictModel):
    number: int
    url: str
    state: Literal["open", "closed", "merged"]
    head_sha: str
    events: list[ReviewEvent] = []
    waiting: str = ""
    stop: bool = False
    ci: list[dict[str, Any]] = []
