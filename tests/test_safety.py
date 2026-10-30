import io
import tarfile

import pytest
from conftest import BASE, FIX, REGRESSION

from oss_contributor.config import Limits, Repository
from oss_contributor.models import Issue
from oss_contributor.process import ContributorError
from oss_contributor.safety import (
    check_regression,
    eligibility,
    safe_path,
    unpack_archive,
    validate_changes,
)


@pytest.mark.parametrize(
    "path",
    [
        "../auth.json",
        "/etc/passwd",
        "a/../../auth.json",
        "a//b",
        "./a",
        "a\\b",
        ".git/config",
        "x/../y",
        "a\nfile",
    ],
)
def test_path_escape_rejected(path):
    with pytest.raises(ContributorError):
        safe_path(path)


@pytest.mark.parametrize("type_", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE])
def test_archive_special_files_rejected(type_):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        member = tarfile.TarInfo("repo/file")
        member.type = type_
        member.linkname = "/Users/example/.codex/auth.json"
        archive.addfile(member)
    with pytest.raises(ContributorError):
        unpack_archive(stream.getvalue())


def test_binary_assets_preserved_without_execution():
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        member = tarfile.TarInfo("repo/logo.png")
        member.size = 3
        archive.addfile(member, io.BytesIO(b"\x89\x00\xff"))
    assert unpack_archive(stream.getvalue())["logo.png"].startswith("\x00base64:")


def test_normal_large_fixture_is_preserved():
    raw = b"\x00" * 700_000
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        member = tarfile.TarInfo("repo/fixtures/sample.bin")
        member.size = len(raw)
        archive.addfile(member, io.BytesIO(raw))
    import base64

    from oss_contributor.safety import BINARY_PREFIX

    encoded = unpack_archive(stream.getvalue())["fixtures/sample.bin"]
    assert base64.b64decode(encoded[len(BINARY_PREFIX) :]) == raw


def test_oversized_archive_member_is_rejected():
    from oss_contributor.safety import MAX_FILE

    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        member = tarfile.TarInfo("repo/fixtures/oversized.bin")
        member.size = MAX_FILE + 1
        archive.addfile(member, io.BytesIO(b"x" * member.size))
    with pytest.raises(ContributorError, match="file too large"):
        unpack_archive(stream.getvalue())


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/test.yml",
        "pyproject.toml",
        "setup.py",
        "conftest.py",
        "tests/test_existing.py",
        ".env",
        "AGENTS.md",
    ],
)
def test_protected_and_existing_tests_cannot_change(path):
    final = {**BASE, "calc.py": FIX, "tests/test_oss_regression_1.py": REGRESSION, path: "pass\n"}
    with pytest.raises(ContributorError):
        validate_changes(BASE, final, "tests/test_oss_regression_1.py", Limits())


@pytest.mark.parametrize(
    "text",
    [
        "import pytest\n@pytest.mark.skip\ndef test_x(): assert False",
        "import pytest\ndef test_x(): pytest.xfail(); assert False",
        "def test_x(): pass",
        "def test_x(): eval('1'); assert True",
    ],
)
def test_regression_cannot_skip_or_remove_assertions(text):
    with pytest.raises(ContributorError):
        check_regression(text)


@pytest.mark.parametrize(
    "policy",
    [
        "No AI contributions.",
        "We do not accept LLM generated fixes.",
        "禁止提交自动修复",
        "AI changes are prohibited. No bots allowed.",
    ],
)
def test_project_policy_is_enforced(policy):
    issue = Issue(
        repo="o/r", number=1, title="Bug", body="Detailed reproduction " * 5, policy=policy
    )
    assert eligibility(issue, Repository(name="o/r", contribution_policy_reviewed=True))


def test_claimed_issue_is_skipped():
    issue = Issue(
        repo="o/r",
        number=1,
        title="Bug",
        body="Detailed reproduction " * 5,
        comments=[{"body": "I'm working on this issue", "authorAssociation": "NONE"}],
    )
    assert "working" in eligibility(
        issue, Repository(name="o/r", contribution_policy_reviewed=True)
    )


def test_cla_requires_explicit_local_prerequisite():
    issue = Issue(
        repo="o/r",
        number=1,
        title="Bug",
        body="Detailed reproduction " * 5,
        policy="Please sign our CLA before contributing.",
    )
    assert "CLA" in eligibility(issue, Repository(name="o/r", contribution_policy_reviewed=True))


def test_line_limit_counts_test_and_source():
    final = {**BASE, "calc.py": FIX, "tests/test_oss_regression_1.py": REGRESSION}
    with pytest.raises(ContributorError, match="limit"):
        validate_changes(BASE, final, "tests/test_oss_regression_1.py", Limits(max_lines=2))
