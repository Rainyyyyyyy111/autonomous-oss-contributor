from __future__ import annotations

import re
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


ACKNOWLEDGEMENT = re.compile(r"\s*(?:thanks!?|thank you!?|lgtm[.!]?|looks good[.!]?)\s*", re.I)


FEEDBACK_GUIDANCE = (
    "Classify all feedback before editing. Use description only when every request explicitly "
    "concerns the PR description or its wording, with no code, test or CI work requested. "
    "Use code for concrete source changes. Broad complaints such as 'AI slop' without a clear "
    "target are ambiguous: return needs_attention with evidence instead of guessing or making "
    "cosmetic code changes. Never treat failed CI as description-only feedback. "
    "For description changes, provide concise, factual Repair summary/root_cause and evidence; "
    "the controller preserves verification results, issue links, templates and AI disclosure. "
    "Do not claim new testing or remove AI disclosure to conceal assistance."
)


class FeedbackPlan(StrictModel):
    action: Literal["code", "description", "needs_attention"]
    reason: str
    evidence: list[str]
    repair: Repair | None = None


class ValidationReport(StrictModel):
    base_sha: str
    evidence_digest: str = ""
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
    url: str = ""
    author: str = ""
    commit_sha: str = ""
    path: str = ""
    review_state: str = ""


class PRState(StrictModel):
    number: int
    url: str
    state: Literal["open", "closed", "merged"]
    head_sha: str
    body: str = ""
    events: list[ReviewEvent] = []
    feedback_history: list[ReviewEvent] = []
    waiting: str = ""
    stop: bool = False
    ci: list[dict[str, Any]] = []
    created_at: str = ""
    closed_at: str = ""
    merged_at: str = ""


class NotificationBatch(StrictModel):
    rows: list[dict[str, Any]] = []
    last_modified: str = ""
    poll_interval: int = 60
    not_modified: bool = False
