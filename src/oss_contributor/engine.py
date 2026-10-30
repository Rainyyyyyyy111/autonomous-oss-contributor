from __future__ import annotations

import hashlib
import json
import time
import xml.etree.ElementTree as ET
from contextlib import nullcontext

from .codex import Codex
from .config import Config, Repository
from .github import GitHub
from .models import Assessment, Issue, Regression, Repair, ValidationReport
from .process import ContributorError, PublicationConflict, QuotaExhausted, RateLimited
from .safety import check_regression, eligibility, validate_changes
from .sandbox import DockerSandbox
from .store import Store

OPEN = ("open", "revising", "publishing", "validated", "needs_attention", "stopped",
        "desktop_pending", "desktop_running", "desktop_validating")


def merged(base: dict[str, str], changes: dict[str, str | None]) -> dict[str, str]:
    result = dict(base)
    for path, content in changes.items():
        if content is None:
            result.pop(path, None)
        else:
            result[path] = content
    return result


def regression_argv(path: str) -> list[str]:
    return [
        "python",
        "-m",
        "pytest",
        "-q",
        "-o",
        "addopts=",
        path,
        "--junitxml=/tmp/oss-regression.xml",
    ]


def check_junit(sandbox, expect_failure: bool):
    result = sandbox.command(
        [
            "/usr/local/bin/python",
            "-I",
            "-c",
            "from pathlib import Path; print(Path('/tmp/oss-regression.xml').read_text())",
        ]
    )
    if result.code:
        raise ContributorError("regression did not produce JUnit evidence")
    if "<!" in result.output:
        raise ContributorError("unsafe XML evidence")
    try:
        root = ET.fromstring(result.output)
        cases = list(root.iter("testcase"))
    except ET.ParseError:
        raise ContributorError("invalid regression JUnit report") from None
    if not cases or any(list(c.iter("error")) or list(c.iter("skipped")) for c in cases):
        raise ContributorError("regression has errors, skipped tests or no collected tests")
    failures = [n for c in cases for n in c.iter("failure")]
    if expect_failure:
        if not failures or any("AssertionError" not in (n.text or "") for n in failures):
            raise ContributorError("original-code regression must fail on a real assertion")
    elif failures:
        raise ContributorError("regression assertions still fail")


class Engine:
    def __init__(self, config: Config, store: Store, github=None, codex=None, sandbox_factory=None):
        self.config, self.store = config, store
        self.github = github if github is not None else GitHub(config.timezone)
        if isinstance(self.github, GitHub):
            self.github.before_write = self.ensure_unpaused
        self.codex = codex if codex is not None else Codex(config)
        self.owner = hashlib.sha256(str(store.home).encode()).hexdigest()[:20]
        self.cleanup_needed = sandbox_factory is None
        if sandbox_factory is None:
            self.sandbox = lambda repo, files, deadline: DockerSandbox(
                repo, files, deadline, owner=self.owner
            )
        else:
            self.sandbox = sandbox_factory

    def discover(self):
        candidates = self.github.discover(self.config.discovery)
        for raw in candidates:
            self.store.candidate(raw["fullName"].lower(), raw)
        # Search is a source of candidates, never authorization.
        for repo in self.config.repositories:
            self.store.candidate(repo.name.lower(), {"allowlisted": True})
            for number in self.github.issue_numbers(
                repo.name, self.config.discovery.issues_per_repo
            ):
                # Full evidence is deliberately refreshed when work starts.
                self.store.create_task(
                    Issue(repo=repo.name.lower(), number=number, title="pending inspection")
                )
        self.store.set_meta("last_discovery", str(time.time()))
        self.store.event(
            None,
            "discovery",
            {"candidates": len(candidates), "allowlisted": len(self.config.repositories)},
        )
        return candidates

    def model(self, schema, prompt, sandbox, deadline, writable=False):
        if self.store.meta("paused"):
            raise ContributorError("paused by user")
        available = self.config.limits.model_seconds_per_day - self.store.usage()["model_seconds"]
        budget = min(deadline - time.monotonic(), available)
        if budget < 1 or not self.store.reserve("model_seconds", budget):
            raise ContributorError("daily model execution budget exhausted")
        day, started = self.store.day(), time.monotonic()
        try:
            return self.codex.ask(
                schema, prompt, sandbox, min(deadline, started + budget), writable
            )
        except QuotaExhausted:
            self.store.set_meta("paused", "Codex quota exhausted; resume after quota is available")
            raise
        finally:
            self.store.refund_model(day, max(0, budget - (time.monotonic() - started)))

    def active(self):
        return [
            t
            for t in self.store.tasks(OPEN)
            if t["status"] != "needs_attention" or t["data"].get("pr")
        ]

    def ensure_unpaused(self):
        if self.store.meta("paused"):
            raise ContributorError("worker is paused")

    def baseline(self, sandbox, repo: Repository, report: ValidationReport):
        report.commands.extend(sandbox.install())
        snapshot = sandbox.snapshot()
        for path, content in sandbox.files.items():
            if snapshot.get(path) != content:
                raise ContributorError(f"installation changed repository file: {path}")
        for argv in repo.tests + repo.checks:
            result = sandbox.command(argv)
            report.commands.append(result)
            if result.code:
                raise ContributorError("baseline tests/checks failed")

    def validate(
        self,
        repo: Repository,
        base: dict[str, str],
        changes: dict[str, str | None],
        path: str,
        sha: str,
        deadline: float,
    ) -> ValidationReport:
        final = merged(base, changes)
        accepted, added, removed = validate_changes(base, final, path, self.config.limits)
        report = ValidationReport(base_sha=sha, files=list(accepted), added=added, removed=removed)
        try:
            with self.sandbox(repo, base, deadline) as clean:
                self.baseline(clean, repo, report)
                clean.apply({path: final[path]})
                red = clean.command(regression_argv(path))
                report.commands.append(red)
                if red.code != 1:
                    raise ContributorError(
                        "regression must fail with pytest exit 1 on original source"
                    )
                check_junit(clean, expect_failure=True)
                clean.apply(accepted)
                green = clean.command(regression_argv(path))
                report.commands.append(green)
                if green.code:
                    raise ContributorError("regression did not pass after applying the fix")
                check_junit(clean, expect_failure=False)
                for argv in repo.tests + repo.checks:
                    result = clean.command(argv)
                    report.commands.append(result)
                    if result.code:
                        raise ContributorError("existing tests/checks failed after fix")
                after = clean.snapshot()
                # Untracked caches are ignored by the bridge, never source/test mutations.
                validated, _, _ = validate_changes(base, after, path, self.config.limits)
                if validated != accepted:
                    raise ContributorError("validation mutated the candidate patch")
            report.passed = True
        except ContributorError as exc:
            report.failure = str(exc)
        return report

    def process(self, task: dict, revision_events: list | None = None):
        deadline = time.monotonic() + self.config.limits.task_seconds
        with self.github.bounded(deadline) if isinstance(self.github, GitHub) else nullcontext():
            self._process(task, revision_events, deadline)

    def _process(self, task: dict, revision_events: list | None, deadline: float):
        task_id, name, number = task["id"], task["repo"], task["issue"]
        repo = self.config.repository(name)
        if repo is None:
            self.store.update(task_id, "skipped", reason="not allowlisted")
            return
        self.ensure_unpaused()
        revision = revision_events is not None
        if not revision:
            active = self.active()
            if len(active) >= self.config.limits.open_prs or any(t["repo"] == name for t in active):
                return
        issue = self.github.issue(name, number)
        if revision:
            own_url = task["data"]["pr"]["url"]
            issue.conflicts = [c for c in issue.conflicts if own_url not in c]
        reason = eligibility(issue, repo)
        if reason:
            self.store.update(task_id, "needs_attention" if revision else "skipped", reason=reason)
            return
        if revision and task["data"].get("revisions", 0) >= self.config.limits.revisions_per_pr:
            self.store.update(task_id, "needs_attention", reason="revision limit reached")
            return
        if not revision and not self.store.reserve("attempts"):
            return
        self.store.update(task_id, "revising" if revision else "running", issue=issue.model_dump())
        report = None
        try:
            branch, sha = self.github.base(name)
            base = self.github.snapshot(name, sha)
            path = f"tests/test_oss_regression_{number}.py"
            if path in base:
                raise ContributorError("reserved regression path already exists upstream")
            self.store.update(task_id, base_sha=sha, base_branch=branch, regression_path=path)
            with self.sandbox(repo, base, deadline) as worker:
                report = ValidationReport(base_sha=sha)
                self.baseline(worker, repo, report)
                if revision:
                    prior = task["data"]["changes"]
                    self.ensure_patch_fits(base, task["data"])
                    worker.apply(prior)
                    regression_content = prior[path]
                else:
                    assessment = self.model(
                        Assessment,
                        "Assess this issue. Inspect code using read/search tools. Return unsuitable if uncertain.\n"
                        + json.dumps(issue.model_dump(), ensure_ascii=False),
                        worker,
                        deadline,
                    )
                    self.store.update(task_id, assessment=assessment.model_dump())
                    if (
                        not assessment.suitable
                        or not assessment.evidence
                        or not assessment.reproduction
                    ):
                        self.store.update(task_id, "skipped", reason=assessment.reason)
                        return
                    regression = self.model(
                        Regression,
                        f"Produce one NEW pytest regression file at {path}. It must import and test the real package, "
                        "contain meaningful assertions, fail with AssertionError on the original code, and not "
                        "modify source, environment, harness, filesystem or configuration.\n"
                        + json.dumps(
                            {"issue": issue.model_dump(), "assessment": assessment.model_dump()},
                            ensure_ascii=False,
                        ),
                        worker,
                        deadline,
                    )
                    check_regression(regression.content)
                    regression_content = regression.content
                    worker.apply({path: regression_content})
                    red = worker.command(regression_argv(path))
                    report.commands.append(red)
                    if red.code != 1:
                        raise ContributorError(
                            "unable to reproduce target bug with an assertion failure"
                        )
                    check_junit(worker, expect_failure=True)
                feedback = [event.model_dump() for event in revision_events or []]
                repair = self.model(
                    Repair,
                    "Fix only this small bug in Python source. Do not change any test or configuration. "
                    "Use repo_apply to edit and sandbox_exec to investigate in the offline container. "
                    "If feedback cannot safely be addressed, make no changes and explain.\n"
                    + json.dumps(
                        {
                            "issue": issue.model_dump(),
                            "regression_path": path,
                            "feedback": feedback,
                        },
                        ensure_ascii=False,
                    ),
                    worker,
                    deadline,
                    writable=True,
                )
                if (
                    not repair.summary.strip()
                    or not repair.root_cause.strip()
                    or not repair.evidence
                ):
                    raise ContributorError(
                        "repair lacks a root cause, summary or supporting evidence"
                    )
                final = worker.snapshot()
                if final.get(path) != regression_content:
                    raise ContributorError("agent changed the fixed regression test")
                changes, _, _ = validate_changes(base, final, path, self.config.limits)
                if revision and changes == task["data"]["changes"]:
                    raise ContributorError("review/CI feedback produced no new source changes")
            report = self.validate(repo, base, changes, path, sha, deadline)
            self.store.update(task_id, report=report.model_dump())
            if not report.passed:
                raise ContributorError(report.failure)
            self.store.update(
                task_id,
                "validated",
                changes=changes,
                repair=repair.model_dump(),
                base_hashes=self.base_hashes(base, changes),
                pending_events=[e.id for e in revision_events or []],
                revisions=task["data"].get("revisions", 0) + int(revision),
            )
            self.publish(self.store.get(task_id), deadline)
        except RateLimited:
            raise
        except PublicationConflict as exc:
            self.store.update(task_id, "needs_attention", reason=str(exc))
        except (ContributorError, OSError, ValueError) as exc:
            if report is not None and not report.passed:
                report.failure = str(exc)
                self.store.update(task_id, report=report.model_dump())
            current = self.store.get(task_id)
            if current["status"] in {"publishing", "validated"} and current["data"].get(
                "report", {}
            ).get("passed"):
                self.store.update(task_id, reason=str(exc), next_reconcile=time.time() + 600)
            else:
                self.store.update(
                    task_id, "needs_attention" if revision else "failed", reason=str(exc)
                )

    @staticmethod
    def base_hashes(base: dict[str, str], changes: dict[str, str | None]) -> dict:
        return {
            p: hashlib.sha256(base[p].encode()).hexdigest() if p in base else None for p in changes
        }

    def ensure_patch_fits(self, base: dict[str, str], data: dict):
        if self.base_hashes(base, data["changes"]) != data["base_hashes"]:
            raise PublicationConflict(
                "upstream changed an affected file; patch needs a fresh diagnosis"
            )

    def body(self, task: dict) -> str:
        data = task["data"]
        report = data["report"]
        issue = Issue.model_validate(data["issue"])
        repair = Repair.model_validate(data["repair"])
        lines = [
            issue.pr_template.strip(),
            f"Fixes #{issue.number}",
            "",
            "## Root cause",
            repair.root_cause,
            "",
            "## Change",
            repair.summary,
            "",
            "## Verification",
            f"Base commit: `{report['base_sha']}`.",
            "The same regression test failed with an assertion on the original source and passed after the fix.",
        ]
        for result in report["commands"]:
            lines.append(f"- `{json.dumps(result['argv'])}` — exit {result['code']}")
        lines += [
            "",
            "This PR was prepared automatically by OSS Contributor using Codex. "
            "Repository commands and independent verification ran in credential-free Docker containers.",
            "Merge decisions remain with the project maintainers.",
            f"<!-- oss-contributor:issue:{issue.number} -->",
        ]
        return "\n".join(lines).strip()

    def publish(self, task: dict, deadline: float):
        with self.github.bounded(deadline) if isinstance(self.github, GitHub) else nullcontext():
            self._publish(task, deadline)

    def _publish(self, task: dict, deadline: float):
        self.ensure_unpaused()
        repo = self.config.repository(task["repo"])
        if repo is None:
            raise PublicationConflict("publication denied: repository removed from allowlist")
        data, task_id = task["data"], task["id"]
        if (
            not data.get("pr")
            and not data.get("publication_reserved")
            and self.store.usage()["publications"] >= self.config.limits.prs_per_day
        ):
            self.store.update(
                task_id,
                "validated",
                reason="daily publication limit reached",
                next_reconcile=time.time() + 600,
            )
            return
        if not data.get("report", {}).get("passed"):
            raise ContributorError("publication requires independent validation")
        fresh = self.github.issue(task["repo"], task["issue"])
        own_url = (data.get("pr") or {}).get("url")
        if own_url:
            fresh.conflicts = [c for c in fresh.conflicts if own_url not in c]
        # A PR may already exist after a create-before-save interruption.
        branch_name = f"oss-contributor/issue-{task['issue']}"
        remote = self.github.find_pr(task["repo"], branch_name)
        if remote and remote["state"] != "open":
            self.store.update(
                task_id, "closed", reason="existing PR was closed; no replacement will be created"
            )
            return
        if remote and data.get("publication_reserved"):
            fresh.conflicts = [c for c in fresh.conflicts if remote["html_url"] not in c]
        reason = eligibility(fresh, repo)
        if reason:
            raise PublicationConflict(f"pre-publication check: {reason}")
        base_branch, latest_sha = self.github.base(task["repo"])
        if latest_sha != data["base_sha"]:
            latest = self.github.snapshot(task["repo"], latest_sha)
            self.ensure_patch_fits(latest, data)
            report = self.validate(
                repo, latest, data["changes"], data["regression_path"], latest_sha, deadline
            )
            if not report.passed:
                raise ContributorError(
                    "patch failed verification on updated upstream: " + report.failure
                )
            self.store.update(
                task_id,
                base_sha=latest_sha,
                base_branch=base_branch,
                base_hashes=self.base_hashes(latest, data["changes"]),
                report=report.model_dump(),
            )
            task = self.store.get(task_id)
            data = task["data"]
            if self.github.base(task["repo"])[1] != latest_sha:
                raise ContributorError("upstream advanced again during verification")
        if (
            not data.get("pr")
            and not remote
            and data.get("publication_reserved") != self.store.day()
        ):
            if not self.store.reserve("publications"):
                self.store.update(
                    task_id,
                    "validated",
                    reason="daily publication limit reached",
                    next_reconcile=time.time() + 600,
                )
                return
            self.store.update(task_id, publication_reserved=self.store.day())
        self.store.update(task_id, "publishing", publication_branch=branch_name)
        result = self.github.publish(
            task["repo"],
            base_branch,
            latest_sha,
            data["changes"],
            branch_name,
            f"fix: {fresh.title}"[:200],
            self.body(task),
            previous_head=(data.get("pr") or {}).get("head_sha"),
        )
        self.store.update(task_id, "open", pr=result, reason="", waiting="")
        self.store.mark_seen(task_id, data.get("pending_events", []))

    def recover(self):
        for task in self.store.tasks(("running", "revising")):
            self.store.update(
                task["id"],
                "needs_attention" if task["data"].get("pr") else "failed",
                reason="interrupted before independent verification; not automatically retried",
            )
        for task in self.store.tasks(("publishing", "validated")):
            if self.store.meta("paused"):
                return
            if time.time() < task["data"].get("next_reconcile", 0):
                continue
            try:
                self.publish(task, time.monotonic() + self.config.limits.task_seconds)
            except RateLimited:
                raise
            except PublicationConflict as exc:
                self.store.update(task["id"], "needs_attention", reason=str(exc))
            except (ContributorError, OSError, ValueError) as exc:
                # Preserve publication intent so a transient failure can reconcile next time.
                self.store.update(task["id"], reason=str(exc), next_reconcile=time.time() + 600)

    def watch(self):
        for task in self.store.tasks(("open", "needs_attention", "stopped")):
            pr = task["data"].get("pr")
            if not pr:
                continue
            remote = self.github.pr_state(task["repo"], pr["number"])
            if remote.state != "open":
                self.store.update(task["id"], remote.state, reason="maintainer decision recorded")
                continue
            if remote.stop:
                self.store.update(task["id"], "stopped", reason="maintainer requested stopping")
                continue
            if remote.head_sha != pr["head_sha"]:
                self.store.update(
                    task["id"], "needs_attention", reason="PR branch changed outside this agent"
                )
                continue
            if task["status"] in {"needs_attention", "stopped"}:
                continue
            unseen = [e for e in remote.events if not self.store.seen(task["id"], e.id)]
            self.store.update(task["id"], waiting=remote.waiting)
            if not unseen or remote.waiting or self.store.meta("paused"):
                continue
            self.process(task, unseen)
        self.store.set_meta("last_watch", str(time.time()))

    def once(self):
        if self.store.meta("paused"):
            return
        # An asynchronous native repair holds the serial model slot across CLI calls.
        if self.store.desktop_jobs():
            return
        try:
            if self.cleanup_needed and self.config.repositories:
                DockerSandbox.cleanup_owner(self.owner)
                self.cleanup_needed = False
            if time.time() < float(self.store.meta("github_retry_after", "0")):
                return
            self.recover()
            now = time.time()
            if (
                now - float(self.store.meta("last_watch", "0"))
                >= self.config.discovery.watch_seconds
            ):
                self.watch()
            if (
                now - float(self.store.meta("last_discovery", "0"))
                >= self.config.discovery.interval_seconds
            ):
                self.discover()
            for task in self.store.tasks(("queued",)):
                if self.store.meta("paused"):
                    break
                self.process(task)
        except RateLimited as exc:
            self.store.set_meta("github_retry_after", str(time.time() + exc.seconds))
            self.store.event(None, "github_backoff", {"seconds": exc.seconds})
