"""Low-cost notification ingestion and triage; never dispatch models or publish patches."""

from __future__ import annotations

import hashlib
import json
import platform
import re
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from .process import ContributorError, RateLimited, run_command

if TYPE_CHECKING:
    from .desktop import Desktop


class NotificationWatcher:
    def __init__(self, desktop: Desktop):
        self.desktop = desktop
        self.engine, self.store, self.config = desktop.engine, desktop.store, desktop.config

    def desktop_alerts(self) -> str | None:
        """Deliver local alerts separately from the coordinator's acknowledgement cursor."""
        if platform.system() != "Darwin" or not self.config.notifications.desktop_alerts:
            return None
        script = (
            "on run argv\n"
            "display notification (item 1 of argv) with title (item 2 of argv)\n"
            "end run"
        )
        for notice in self.desktop.result()["notifications"]:
            if notice.get("kind") not in {
                "closed", "merged", "stopped", "needs_attention", "github_feedback", "ci_completed",
            }:
                continue
            if self.store.db.execute(
                "SELECT 1 FROM notification_alerts WHERE notification=?", (notice["id"],)
            ).fetchone():
                continue
            detail = (notice.get("decision") or notice["kind"]) + ": " + (
                notice.get("url") or notice.get("reason", "See OSS Contributor for details.")
            )
            try:
                result = run_command([
                    "/usr/bin/osascript", "-e", script, "--", detail[:400], "OSS Contributor",
                ], timeout=10)
            except (ContributorError, OSError):
                return "macOS notification delivery failed; triage remains in the durable outbox"
            if result.code:
                return "macOS notification delivery failed; triage remains in the durable outbox"
            with self.store.db:
                self.store.db.execute(
                    "INSERT OR IGNORE INTO notification_alerts VALUES(?,?)", (notice["id"], time.time()),
                )
        return None

    def targets(self) -> dict[int, dict]:
        allowed = self.desktop.scoped_ids()
        result = {}
        for task in self.store.tasks():
            if allowed is not None and task["id"] not in allowed:
                continue
            if not task["data"].get("pr") or not self.config.repository(task["repo"]):
                continue
            self.desktop.ensure_scope(task)
            result[task["id"]] = task
        return result

    def ingest(self, rows: list[dict], targets: dict[int, dict]):
        for row in rows:
            subject = row.get("subject") or {}
            repository = row.get("repository") or {}
            if not isinstance(subject, dict) or not isinstance(repository, dict):
                raise ContributorError("invalid GitHub notification subject/repository")
            repo = repository.get("full_name", "")
            url = subject.get("url") or ""
            if not isinstance(repo, str) or not isinstance(url, str):
                raise ContributorError("invalid GitHub notification subject identity")
            repo, url = repo.lower(), url.lower()
            ident, updated = row.get("id"), row.get("updated_at")
            if not isinstance(ident, str) or not isinstance(updated, str):
                raise ContributorError("GitHub notification lacks thread/version identity")
            for task in targets.values():
                if repo != task["repo"].lower():
                    continue
                pr = task["data"]["pr"]["number"]
                expected = f"https://api.github.com/repos/{repo}"
                matches = (
                    subject.get("type") == "PullRequest" and url == f"{expected}/pulls/{pr}"
                    or subject.get("type") == "Issue" and url == f"{expected}/issues/{task['issue']}"
                    # Suite notifications have no PR number; refresh only this repo's known PRs.
                    or subject.get("type") == "CheckSuite"
                )
                if not matches:
                    continue
                fingerprint = hashlib.sha256(json.dumps(
                    [ident, updated, task["id"], repo, url], separators=(",", ":")
                ).encode()).hexdigest()
                self.store.db.execute(
                    "INSERT OR IGNORE INTO github_inbox VALUES(?,?,?,?,?,?)",
                    (fingerprint, task["id"], ident, updated, "pending", time.time()),
                )

    def observe(self, targets: dict[int, dict], sweep: bool = False) -> list[dict]:
        pending = self.store.db.execute(
            "SELECT DISTINCT task FROM github_inbox WHERE decision='pending' ORDER BY created"
        ).fetchall()
        task_ids = {row["task"] for row in pending}
        if sweep:
            task_ids.update(t["id"] for t in targets.values() if t["status"] not in {"closed", "merged"})
        results = []
        for ident in sorted(task_ids):
            if ident not in targets:
                continue  # Another scope may own this durable inbox item.
            task = self.store.get(ident)
            pr = task["data"]["pr"]
            remote = self.engine.github.pr_state(task["repo"], pr["number"])
            events = self.desktop._observation(task, remote)
            current = self.store.get(ident)
            decision = "unchanged"
            if current["status"] in {"closed", "merged", "stopped", "needs_attention"}:
                decision = current["status"]
            elif remote.waiting:
                decision = "waiting"
            elif events:
                decision = "ci_failed" if any(e.kind == "ci" for e in events) else "review_pending"
                # A broad quality complaint is not permission to manufacture a code revision.
                ambiguous = any(
                    e.kind == "review" and re.search(r"\bAI[ -]+slop\b", e.body, re.I)
                    and not re.search(r"\b(description|PR body|pull request body)\b", e.body, re.I)
                    for e in events
                )
                if ambiguous:
                    decision = "needs_attention"
                    self.store.update(
                        ident, "needs_attention",
                        reason="maintainer quality feedback has no clear target; review required",
                    )
                else:
                    self.store.update(ident, github_notification_pending=True)
                event_ids = sorted(e.id for e in events)
                digest = hashlib.sha256(json.dumps(event_ids).encode()).hexdigest()
                self.store.notify(
                    f"github-feedback:{ident}:{digest}", task=ident,
                    kind="needs_attention" if ambiguous else "github_feedback",
                    decision=decision, url=pr["url"],
                    reason="; ".join(e.body[:300] for e in events)[:1000],
                )
            with self.store.db:
                self.store.db.execute(
                    "UPDATE github_inbox SET decision=? WHERE task=? AND decision='pending'",
                    (decision, ident),
                )
            if decision != "unchanged":
                self.store.event(ident, "github_notification_triage", {"decision": decision})
                results.append({"task": ident, "decision": decision})
        return results

    def poll(self) -> dict:
        now = time.time()
        if not self.config.notifications.enabled:
            return {"state": "disabled", "decisions": []}
        if self.store.meta("paused"):
            return {"state": "paused", "decisions": []}
        if now < float(self.store.meta("github_retry_after", "0")):
            return {"state": "backoff", "decisions": []}
        if self.store.meta("github_notifications_error") and now < float(
            self.store.meta("github_notifications_next_poll", "0")
        ):
            return {"state": "error_backoff", "decisions": []}
        try:
            targets = self.targets()
            if not targets:
                return {"state": "idle", "decisions": []}
            # Retry persisted observations even when the next feed request returns 304.
            sweep = now - float(self.store.meta("github_notifications_last_sweep", "0")) >= self.config.discovery.watch_seconds
            decisions = self.observe(targets, sweep=sweep)
            if sweep:
                self.store.set_meta("github_notifications_last_sweep", str(time.time()))
            if now < float(self.store.meta("github_notifications_next_poll", "0")):
                return {"state": "waiting", "decisions": decisions}
            since = self.store.meta("github_notifications_since") or (
                datetime.now(UTC) - timedelta(days=self.config.notifications.lookback_days)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            previous_validator = self.store.meta("github_notifications_last_modified")
            batch = self.engine.github.notification_batch(
                since, previous_validator,
                self.config.notifications.max_pages,
            )
            next_since = since
            for item in batch.rows:
                stamp = item.get("updated_at")
                if not isinstance(stamp, str):
                    raise ContributorError("GitHub notification lacks update timestamp")
                stamp_time = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
                candidate = (stamp_time - timedelta(seconds=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
                next_since = max(next_since, candidate)
            # Commit inbox and feed cursor together, before any fallible PR observation.
            with self.store.db:
                self.ingest(batch.rows, targets)
                for key, value in {
                    "github_notifications_since": next_since,
                    "github_notifications_last_modified": (
                        batch.last_modified or (previous_validator if batch.not_modified else "")
                    ) if next_since == since else "",
                    "github_notifications_next_poll": str(time.time() + max(
                        batch.poll_interval, self.config.notifications.poll_seconds,
                    )),
                    "github_notifications_last_poll": str(time.time()),
                    "github_notifications_error": "",
                }.items():
                    self.store.db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, value))
            decisions += self.observe(targets)
            return {"state": "not_modified" if batch.not_modified else "polled", "decisions": decisions}
        except RateLimited as exc:
            self.store.set_meta("github_retry_after", str(time.time() + exc.seconds))
            return {"state": "backoff", "decisions": [], "reason": str(exc)}
        except (ContributorError, OSError, ValueError) as exc:
            reason = str(exc)[:1000]
            self.store.set_meta("github_notifications_error", reason)
            delay = max(
                600, self.config.notifications.poll_seconds,
                getattr(self.engine.github, "notification_poll_interval", 60),
            )
            self.store.set_meta("github_notifications_next_poll", str(time.time() + delay))
            self.store.notify(
                "github-notifications-error:" + hashlib.sha256(reason.encode()).hexdigest(),
                kind="needs_attention", reason=reason,
            )
            return {"state": "error", "decisions": [], "reason": reason}
