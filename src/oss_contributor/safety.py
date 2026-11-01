from __future__ import annotations

import ast
import base64
import difflib
import io
import re
import tarfile
from pathlib import PurePosixPath

from .config import Limits, Repository
from .errors import PermissionDenied, ValidationFailure
from .models import Issue
from .process import ContributorError

MAX_FILE = 4 * 1024 * 1024
MAX_ARCHIVE = 40_000_000
BINARY_PREFIX = "\x00base64:"
IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "node_modules",
    ".tox",
    ".nox",
    "build",
    "dist",
}
PROTECTED = re.compile(
    r"(^|/)(\.github|\.gitlab|\.circleci|\.buildkite|\.codex|\.agents|\.git|"
    r"\.env[^/]*|credentials[^/]*|secrets?[^/]*|AGENTS\.md|CLAUDE\.md|"
    r"pyproject\.toml|setup\.(py|cfg)|tox\.ini|pytest\.ini|conftest\.py|"
    r"requirements[^/]*|[^/]*lock[^/]*|Dockerfile[^/]*|Makefile|\.pre-commit-config\.yaml)(/|$)",
    re.I,
)
STOP = re.compile(
    r"\b(stop|do not|don't|no longer)\b.{0,55}\b(bot|agent|automat|contribut|working)|"
    r"\bplease (close|withdraw)\b|停止.*(机器人|贡献|修复)",
    re.I | re.S,
)
CLAIM = re.compile(
    r"\b(i(?:'m| am|’m|'ll| will)?|we(?:'re| are)?)\s+"
    r"(?:currently\s+)?(?:working on|taking|will take|can take|would like to (?:work|take))|"
    r"\b(?:assigned to|in progress|PR (?:is |already )?opened)\b|"
    r"我.*(正在修|来修|处理这个|认领)",
    re.I,
)
BAN = re.compile(
    r"\b(no|not|don't|do not|prohibit\w*|ban\w*|reject\w*)\b[^.\n]{0,100}"
    r"\b(ai|llm|automat\w*|bots?)\b|"
    r"\b(ai|llm|bots?|automat\w*)\b[^.\n]{0,100}\b(prohibit\w*|banned|not accepted|not allowed)\b|"
    r"禁止[^\n]{0,40}(AI|自动|机器人)",
    re.I,
)


def safe_path(path: str) -> str:
    p = PurePosixPath(path)
    if (
        not path
        or len(path) > 300
        or path != p.as_posix()
        or p.is_absolute()
        or any(part in {"", ".", ".."} for part in path.split("/"))
        or any(ord(c) < 32 for c in path)
        or "\\" in path
        or any(part in IGNORED_DIRS for part in p.parts)
    ):
        raise PermissionDenied(f"unsafe repository path: {path!r}")
    return path


def unpack_archive(payload: bytes) -> dict[str, str]:
    """Never extract an untrusted tar to the host filesystem."""
    if len(payload) > MAX_ARCHIVE:
        raise ContributorError("repository archive too large")
    files: dict[str, str] = {}
    total = 0
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        for index, member in enumerate(archive):
            if index > 30_000:
                raise ContributorError("too many archive entries")
            parts = PurePosixPath(member.name).parts
            if len(parts) < 2:
                continue
            path = "/".join(parts[1:])
            if any(p in IGNORED_DIRS for p in parts[1:]):
                continue
            safe_path(path)
            if member.isdir():
                continue
            if not member.isfile():
                raise ContributorError("symlinks, hardlinks and special files are unsupported")
            if member.size > MAX_FILE:
                raise ContributorError(f"repository file too large: {path}")
            total += member.size
            if total > MAX_ARCHIVE:
                raise ContributorError("expanded archive too large")
            stream = archive.extractfile(member)
            assert stream is not None
            raw = stream.read(MAX_FILE + 1)
            try:
                content = raw.decode("utf-8")
                if "\x00" in content:
                    content = BINARY_PREFIX + base64.b64encode(raw).decode()
            except UnicodeDecodeError:
                content = BINARY_PREFIX + base64.b64encode(raw).decode()
            if path in files:
                raise ContributorError("duplicate archive entry")
            files[path] = content
    return files


def eligibility(issue: Issue, repo: Repository) -> str | None:
    if issue.state.lower() != "open":
        return "issue is no longer open"
    if issue.conflicts:
        return "; ".join(issue.conflicts)
    if not repo.contribution_policy_reviewed:
        return "repository contribution policy needs one-time local review"
    if BAN.search(issue.policy):
        return "repository policy restricts AI/automated contributions"
    if (
        re.search(r"\bCLA\b|contributor licen[cs]e agreement", issue.policy, re.I)
        and not repo.cla_completed
    ):
        return "CLA prerequisite has not been completed"
    if len(issue.body.strip()) < 40:
        return "issue lacks sufficient reproduction detail"
    for comment in issue.comments:
        text = comment.get("body", "")
        if CLAIM.search(text):
            return "a public comment indicates someone may be working on the issue"
        if STOP.search(text) and comment.get("authorAssociation") in {
            "OWNER",
            "MEMBER",
            "COLLABORATOR",
        }:
            return "maintainer requested stopping automated work"
    return None


def is_test(path: str) -> bool:
    name = PurePosixPath(path).name
    return (
        any(p in {"test", "tests", "testing"} for p in PurePosixPath(path).parts)
        or name.startswith("test_")
        or name.endswith("_test.py")
    )


def check_regression(content: str):
    try:
        tree = ast.parse(content)
    except SyntaxError as exc:
        raise ValidationFailure(f"invalid regression test: {exc}") from exc
    if not any(isinstance(n, ast.Assert) for n in ast.walk(tree)):
        raise ValidationFailure("regression must contain assertions")
    if not any(
        isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_")
        for n in ast.walk(tree)
    ):
        raise ValidationFailure("regression must contain pytest tests")
    if re.search(
        r"\b(skip|skipif|xfail|importorskip|exec|eval|__import__)\b|"
        r"pytest_plugins|sys\.modules|os\.environ|subprocess|socket",
        content,
    ):
        raise ValidationFailure("regression contains skipping or unsafe harness manipulation")


def validate_changes(
    base: dict[str, str], final: dict[str, str], regression_path: str, limits: Limits
) -> tuple[dict[str, str | None], int, int]:
    changes: dict[str, str | None] = {}
    added = removed = 0
    for path in sorted(base.keys() | final.keys()):
        if base.get(path) == final.get(path):
            continue
        safe_path(path)
        if PROTECTED.search(path):
            raise ContributorError(f"protected file changed: {path}")
        if is_test(path) and path != regression_path:
            raise ContributorError(f"existing or unrelated test changed: {path}")
        if path != regression_path and not path.endswith(".py"):
            raise ContributorError(f"v1 only changes Python source files: {path}")
        content = final.get(path)
        if content is not None:
            if len(content.encode()) > MAX_FILE:
                raise ContributorError("changed file too large")
            if re.search(
                r"\b(?:pytest\.)?(?:skip|skipif|xfail|importorskip)\b|#\s*(?:noqa|type:\s*ignore)",
                content,
            ) and content != base.get(path):
                # Compare additions rather than rejecting pre-existing suppressions below.
                for line in difflib.ndiff(base.get(path, "").splitlines(), content.splitlines()):
                    if line.startswith("+ ") and re.search(
                        r"\b(skip|skipif|xfail|importorskip)\b|noqa|type:\s*ignore", line
                    ):
                        raise ContributorError("patch adds skipping or suppressions")
            try:
                ast.parse(content)
            except SyntaxError:
                raise ContributorError(f"patch contains invalid Python: {path}") from None
        changes[path] = content
        for line in difflib.ndiff(base.get(path, "").splitlines(), (content or "").splitlines()):
            added += line.startswith("+ ")
            removed += line.startswith("- ")
    if regression_path not in changes or regression_path in base or not final.get(regression_path):
        raise ContributorError("a new regression test is required")
    check_regression(final[regression_path])
    if not any(p != regression_path for p in changes):
        raise ContributorError("no source fix was produced")
    if len(changes) > limits.max_files or added + removed > limits.max_lines:
        raise ContributorError("patch exceeds configured file/line limit")
    return changes, added, removed
