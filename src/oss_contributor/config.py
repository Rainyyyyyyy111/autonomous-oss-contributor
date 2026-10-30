from __future__ import annotations

import re
import tomllib
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator

from .models import StrictModel


def default_home() -> Path:
    return Path.home() / ".local/share/oss-contributor"


class Limits(StrictModel):
    attempts_per_day: int = Field(3, ge=1, le=100)
    prs_per_day: int = Field(1, ge=1, le=20)
    task_seconds: int = Field(1800, ge=1, le=7200)
    model_seconds_per_day: int = Field(7200, ge=1)
    revisions_per_pr: int = Field(2, ge=0, le=10)
    open_prs: int = Field(3, ge=1, le=20)
    max_files: int = Field(5, ge=2, le=20)
    max_lines: int = Field(200, ge=1, le=2000)


class Discovery(StrictModel):
    min_stars: int = Field(500, ge=0)
    pushed_days: int = Field(30, ge=1)
    max_repositories: int = Field(50, ge=1, le=100)
    issues_per_repo: int = Field(10, ge=1, le=100)
    interval_seconds: int = Field(3600, ge=1)
    watch_seconds: int = Field(600, ge=1)


class Notifications(StrictModel):
    enabled: bool = True
    desktop_alerts: bool = True
    poll_seconds: int = Field(60, ge=60, le=3600)
    lookback_days: int = Field(7, ge=1, le=90)
    max_pages: int = Field(10, ge=1, le=100)


class Repository(StrictModel):
    name: str
    image: str = "oss-contributor-runner:0.1"
    install: list[list[str]] = [["python", "-m", "pip", "install", "-e", ".[test]"]]
    tests: list[list[str]] = [["python", "-m", "pytest", "-q"]]
    checks: list[list[str]] = []
    # A human configures repository-specific policy prerequisites once, never the model.
    contribution_policy_reviewed: bool = False
    cla_completed: bool = False

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value) or any(
            part in {".", ".."} for part in value.split("/")
        ):
            raise ValueError("repository must be owner/name")
        return value

    @field_validator("image")
    @classmethod
    def valid_image(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:@-]*", value):
            raise ValueError("invalid image reference")
        return value

    @field_validator("install", "tests", "checks")
    @classmethod
    def valid_commands(cls, value: list[list[str]]) -> list[list[str]]:
        if any(not argv or any(not arg or "\x00" in arg for arg in argv) for argv in value):
            raise ValueError("commands must be nonempty argument arrays")
        return value

    @field_validator("tests")
    @classmethod
    def require_tests(cls, value: list[list[str]]) -> list[list[str]]:
        if not value:
            raise ValueError("at least one baseline test command is required")
        return value


class Config(StrictModel):
    state_dir: Path = Field(default_factory=default_home)
    timezone: str = "Asia/Singapore"
    codex_version: str = "0.140.0"
    model: str | None = None
    limits: Limits = Field(default_factory=lambda: Limits())
    discovery: Discovery = Field(default_factory=lambda: Discovery())
    notifications: Notifications = Field(default_factory=lambda: Notifications())
    repositories: list[Repository] = []

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @field_validator("repositories")
    @classmethod
    def unique_repositories(cls, value: list[Repository]) -> list[Repository]:
        names = [r.name.lower() for r in value]
        if len(set(names)) != len(names):
            raise ValueError("duplicate repository")
        return value

    def repository(self, name: str) -> Repository | None:
        return next((r for r in self.repositories if r.name.lower() == name.lower()), None)


TEMPLATE = """# No repository is authorized until explicitly added below.
timezone = "Asia/Singapore"
codex_version = "0.140.0"
# model = "your available Codex model"  # otherwise use the account default

[limits]
attempts_per_day = 3
prs_per_day = 1
task_seconds = 1800
model_seconds_per_day = 7200
revisions_per_pr = 2
open_prs = 3
max_files = 5
max_lines = 200

[discovery]
min_stars = 500
pushed_days = 30
max_repositories = 50
issues_per_repo = 10
interval_seconds = 3600
watch_seconds = 600

[notifications]
enabled = true
desktop_alerts = true
poll_seconds = 60
lookback_days = 7
max_pages = 10

# [[repositories]]
# name = "your-account/your-test-repository"
# image = "oss-contributor-runner:0.1"
# install = [["python", "-m", "pip", "install", "-e", ".[test]"]]
# tests = [["python", "-m", "pytest", "-q"]]
# checks = [["python", "-m", "ruff", "check", "."]]
# contribution_policy_reviewed = true
# cla_completed = false
"""


def load_config(path: Path) -> Config:
    with path.open("rb") as stream:
        return Config.model_validate(tomllib.load(stream))
