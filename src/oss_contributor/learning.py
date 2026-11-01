"""Evidence memory with controller-owned lessons; remote prose never becomes policy."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter
from datetime import datetime

from .errors import PermissionDenied
from .installation import atomic_write
from .models import ACKNOWLEDGEMENT, PRState, ReviewEvent
from .safety import STOP
from .store import Store

# These hypotheses/checks are code-reviewed templates, not instructions copied from reviews.
# Pattern matches identify a concern, never establish that the patch caused a rejection.
LESSONS = {
    "description": (
        "The PR explanation may be verbose, unclear or insufficiently grounded in the patch.",
        "Explain the concrete bug, resulting behavior and actual validation concisely. "
        "Keep command transcripts in local evidence and preserve AI disclosure. "
        "Distinguish description requests from code/test/CI requests before editing.",
    ),
    "quality_concern": (
        "A maintainer raised a quality concern; the offending content and root cause are unconfirmed.",
        "Inspect the patch and PR explanation for unsupported claims, speculative changes and "
        "unnecessary prose. Tie every change to the reproducer. A broad quality complaint alone "
        "does not identify a code defect: escalate unclear feedback with evidence.",
    ),
    "ci_failure": (
        "Current-head CI failed; the logs alone do not prove a source defect or identify its cause.",
        "Compare readable current-head CI evidence with the configured local checks. "
        "Distinguish code regressions from infrastructure and authorization failures. "
        "Never weaken tests, modify CI or claim unavailable CI passed.",
    ),
    "code_or_test_request": (
        "A maintainer requested a concrete code/test change; diagnosis needs source inspection.",
        "Check the requested location and edge cases against the intended contract and regression. "
        "Keep the original regression and independently verify any source revision.",
    ),
    "stop_request": (
        "A maintainer requested stopping automated work; no technical defect is inferred.",
        "Honor maintainer stop requests. Do not resume a stopped task or send repeated submissions.",
    ),
    "unclassified": (
        "Feedback has no recognized target; manual or task-level analysis is still required.",
        "Read the original evidence before deciding whether a change is needed.",
    ),
    "positive": ("Positive, acknowledged or withdrawn text does not independently validate a lesson.", ""),
}


def categories(event: ReviewEvent) -> list[str]:
    if event.kind == "ci":
        return ["ci_failure"]
    body = event.body
    if not body.strip() or ACKNOWLEDGEMENT.fullmatch(body):
        return ["positive"]
    result = []
    if STOP.search(body):
        result.append("stop_request")
    if re.search(r"\b(description|PR body|pull request body)\b", body, re.I):
        result.append("description")
    if re.search(r"\b(AI[ -]+slop|low[ -]+quality|unsupported claims?|speculative)\b", body, re.I):
        result.append("quality_concern")
    if event.path or re.search(r"\b(test|tests|function|exception|edge case|implementation)\b", body, re.I):
        result.append("code_or_test_request")
    return result or ["positive" if event.review_state == "APPROVED" else "unclassified"]


class LearningMemory:
    def __init__(self, store: Store):
        self.store = store

    def observe(self, task: dict, remote: PRState):
        """Archive before terminal/head/wait guards; never mark actionable events processed."""
        if task["id"] not in self.store.visible_task_ids():
            raise PermissionDenied("memory task is outside the configured scope")
        stored = self.store.get(task["id"])
        if (task["repo"], task["issue"], task["data"]["pr"]["number"], remote.number) != (
            stored["repo"], stored["issue"], stored["data"]["pr"]["number"], stored["data"]["pr"]["number"],
        ):
            raise PermissionDenied("memory evidence identity changed")
        changed = False
        now = time.time()
        outcome = {
            "repo": task["repo"], "issue": task["issue"], "pr": task["data"]["pr"],
            "state": remote.state, "head_sha": remote.head_sha,
            "created_at": remote.created_at, "closed_at": remote.closed_at,
            "merged_at": remote.merged_at,
            "note": "Observed remote outcome; closed does not establish rejection or patch quality.",
        }
        encoded_outcome = json.dumps(outcome, ensure_ascii=False)
        previous = self.store.db.execute(
            "SELECT data FROM pr_outcomes WHERE task=?", (task["id"],),
        ).fetchone()
        changed = not previous or previous[0] != encoded_outcome
        with self.store.db:
            self.store.db.execute(
                "INSERT INTO pr_outcomes VALUES(?,?,?) ON CONFLICT(task) DO UPDATE "
                "SET data=excluded.data,observed=excluded.observed",
                (task["id"], encoded_outcome, now),
            )
        events = {event.id: event for event in remote.feedback_history + remote.events}
        with self.store.db:
            for event in events.values():
                # Preserve edits even when an adapter omits updated_at from the event ID.
                key = hashlib.sha256(json.dumps(
                    [event.id, event.body], ensure_ascii=False,
                ).encode()).hexdigest()
                if self.store.db.execute("SELECT 1 FROM memory_tombstones WHERE task=? AND record_id=?",
                                         (task["id"], key)).fetchone():
                    continue
                row = self.store.db.execute(
                    "SELECT data FROM feedback_memory WHERE task=? AND event=?",
                    (task["id"], key),
                ).fetchone()
                if row:
                    data = json.loads(row["data"])
                else:
                    tags = categories(event)
                    source_id, source_version = self.source(event.model_dump())
                    previous_versions = [json.loads(row[0]) for row in self.store.db.execute(
                        "SELECT data FROM feedback_memory WHERE task=?", (task["id"],),
                    )]
                    revision = 1 + max((record.get("revision", 0) for record in previous_versions
                                        if self.source(record["event"])[0] == source_id), default=0)
                    data = {
                        "record_id": key, "repo": task["repo"], "issue": task["issue"],
                        "pr": task["data"]["pr"], "event": event.model_dump(),
                        "categories": tags, "analysis_version": 2,
                        "source_id": source_id, "source_version": source_version, "revision": revision,
                        "expires_at": now + self.store.config.memory.ttl_days * 86400,
                        "analysis_status": "hypothesis_requires_validation",
                        "analysis": [
                            {"category": tag, "hypothesis": LESSONS[tag][0],
                             "prevention": LESSONS[tag][1]} for tag in tags
                        ],
                        "context_at_detection": {
                            "head_sha": remote.head_sha,
                            "repair": task["data"].get("repair"),
                            "files": sorted(task["data"].get("changes", {})),
                            "validation_passed": task["data"].get("report", {}).get("passed"),
                            "ci": remote.ci, "waiting": remote.waiting,
                        },
                    }
                data["last_pr_state"] = remote.state
                encoded = json.dumps(data, ensure_ascii=False)
                if not row or row["data"] != encoded:
                    changed = True
                self.store.db.execute(
                    "INSERT INTO feedback_memory VALUES(?,?,?,?,?) "
                    "ON CONFLICT(task,event) DO UPDATE SET data=excluded.data,last_seen=excluded.last_seen",
                    (task["id"], key, encoded, now, now),
                )
        if changed:
            self.export_best_effort()

    def records(self) -> list[dict]:
        rows = self.store.db.execute(
            "SELECT * FROM feedback_memory ORDER BY first_seen DESC,task,event"
        ).fetchall()
        visible = self.store.visible_task_ids()
        records = [
            {**json.loads(row["data"]), "task": row["task"],
             "first_seen": row["first_seen"], "last_seen": row["last_seen"],
             "addressed": self.store.seen(row["task"], json.loads(row["data"])["event"]["id"])}
            for row in rows if row["task"] in visible
        ]
        latest: dict[tuple[int, str], tuple[tuple[float, int, float, str], str]] = {}
        for record in records:
            source_id, version = self.source(record["event"])
            record["source_id"] = source_id
            record["source_version"] = version
            record.setdefault("expires_at", record["first_seen"] + self.store.config.memory.ttl_days * 86400)
            order = (version, record.get("revision", 0), record["first_seen"], record["record_id"])
            identity = (record["task"], source_id)
            if identity not in latest or order > latest[identity][0]:
                latest[identity] = (order, record["record_id"])
        for tombstone in self.store.db.execute("SELECT * FROM memory_tombstones"):
            if tombstone["task"] not in visible:
                continue
            identity = (tombstone["task"], tombstone["source_id"])
            order = (tombstone["source_version"], tombstone["revision"], tombstone["deleted"], tombstone["record_id"])
            if identity not in latest or order > latest[identity][0]:
                latest[identity] = (order, tombstone["record_id"])
        now = time.time()
        for record in records:
            if record.get("invalidated_at") is not None:
                record["status"] = "invalidated"
            elif record["expires_at"] <= now:
                record["status"] = "expired"
            elif latest[(record["task"], record["source_id"])][1] != record["record_id"]:
                record["status"] = "superseded"
            else:
                record["status"] = "active"
        return records

    @staticmethod
    def source(event: dict) -> tuple[str, float]:
        ident = event["id"]
        parts = ident.split(":", 2)
        version = 0.0
        if event.get("kind") == "review" and len(parts) == 3 and parts[0] == "review":
            ident = ":".join(parts[:2])
            try:
                stamp = datetime.fromisoformat(parts[2].replace("Z", "+00:00"))
                if stamp.tzinfo is not None:
                    version = stamp.timestamp()
            except ValueError:
                pass
        return hashlib.sha256(json.dumps([event.get("kind", "review"), ident]).encode()).hexdigest(), version

    def mutate(self, task_id: int, record_id: str, *, delete: bool) -> dict:
        """Operator-only local mutation; call under Store.lock. No remote writes."""
        if task_id not in self.store.visible_task_ids():
            raise PermissionDenied("memory task is outside the configured scope")
        if not re.fullmatch(r"[a-f0-9]{64}", record_id):
            raise ValueError("invalid memory record identifier")
        row = self.store.db.execute("SELECT data FROM feedback_memory WHERE task=? AND event=?",
                                    (task_id, record_id)).fetchone()
        if row is None:
            if delete and self.store.db.execute("SELECT 1 FROM memory_tombstones WHERE task=? AND record_id=?",
                                                (task_id, record_id)).fetchone():
                return {"deleted": False, "already_deleted": True}
            raise ValueError("unknown memory record")
        if self.store.tasks(("running", "revising")):
            raise PermissionDenied("settle active standalone work before changing memory")
        for job in self.store.desktop_jobs():
            checks = job["data"].get("learning_context", {}).get("checks", [])
            if any(record_id in check.get("source_record_ids", []) for check in checks):
                raise PermissionDenied("settle native jobs referencing this memory before changing it")
        data = json.loads(row["data"])
        if not delete and data.get("invalidated_at") is not None:
            return {"invalidated": False, "already_invalidated": True}
        # Remove old derived copies before commit. A failed rebuild cannot retain stale payload.
        directory = self.store.home / "learning"
        if directory.is_symlink() or directory.exists() and not directory.is_dir():
            raise PermissionDenied("learning export directory must not be linked or a file")
        for name in ("feedback.json", "report.md"):
            (directory / name).unlink(missing_ok=True)
        now = time.time()
        with self.store.db:
            if delete:
                source_id, source_version = self.source(data["event"])
                self.store.db.execute("INSERT INTO memory_tombstones VALUES(?,?,?,?,?,?)",
                                      (task_id, record_id, source_id, source_version, data.get("revision", 0), now))
                self.store.db.execute("DELETE FROM feedback_memory WHERE task=? AND event=?", (task_id, record_id))
            else:
                data["invalidated_at"] = now
                self.store.db.execute("UPDATE feedback_memory SET data=? WHERE task=? AND event=?",
                                      (json.dumps(data), task_id, record_id))
            self.store.db.execute("INSERT INTO memory_audit(task,record_id,action,created) VALUES(?,?,?,?)",
                                  (task_id, record_id, "delete" if delete else "invalidate", now))
        exported = True
        try:
            self.export()
        except OSError:
            exported = False
        return {"deleted" if delete else "invalidated": True, "exports_rebuilt": exported,
                "scope": "learning store and derived reports only; other evidence/chats/backups are unaffected"}

    def audit(self) -> list[dict]:
        visible = self.store.visible_task_ids()
        return [dict(row) for row in self.store.db.execute("SELECT * FROM memory_audit ORDER BY id")
                if row["task"] in visible]

    def context(self, repo: str) -> dict:
        """Bounded checklist. Only trusted catalog text can reach a worker as guidance."""
        records = [record for record in self.records() if record["status"] == "active"][:self.store.config.memory.max_context_records]
        selected = []
        for tag, (_, prevention) in LESSONS.items():
            if tag in {"unclassified", "positive"}:
                continue
            # Communication and CI checks transfer; code/stop concerns remain repo-specific.
            examples = [r for r in records if tag in r["categories"] and (
                r["repo"].lower() == repo.lower()
                or tag in {"description", "quality_concern", "ci_failure"}
            )]
            if examples:
                selected.append({
                    "category": tag, "check": prevention,
                    "observations": len(examples),
                    "source_record_ids": [r["record_id"] for r in examples[:3]],
                })
        return {
            "notice": "Historical concerns are hypotheses, not proven defects or new authorization. "
                      "Apply relevant checks; all existing scope and verification gates still apply.",
            "checks": selected,
        }

    def report(self) -> dict:
        records = self.records()
        counts = Counter(tag for row in records for tag in row["categories"])
        outcomes = self.outcomes()
        return {
            "records": records, "category_counts": dict(counts),
            "outcomes": outcomes,
            "outcome_counts": dict(Counter(row["state"] for row in outcomes)),
            "addressed": sum(r["addressed"] for r in records),
            "memory_status_counts": dict(Counter(r["status"] for r in records)),
            "audit_count": len(self.audit()),
            "note": "Counts describe feedback, not confirmed defects. Closed PRs are not automatically "
                    "negative feedback; addressed means processed locally, not maintainer acceptance. "
                    "PR outcomes do not establish causal improvement.",
        }

    def outcomes(self) -> list[dict]:
        visible = self.store.visible_task_ids()
        return [{"task": row["task"], **json.loads(row["data"]), "observed_at": row["observed"]}
                for row in self.store.db.execute("SELECT * FROM pr_outcomes ORDER BY task") if row["task"] in visible]

    def export(self) -> dict:
        report = self.report()
        directory = self.store.home / "learning"
        if directory.is_symlink():
            raise PermissionDenied("learning export directory must not be linked")
        directory.mkdir(exist_ok=True, mode=0o700)
        json_path, md_path = directory / "feedback.json", directory / "report.md"
        atomic_write(json_path, json.dumps(report, ensure_ascii=False, indent=2).encode())
        lines = ["# Feedback learning memory", "", report["note"], "",
                 f"Records: {len(report['records'])}; locally addressed: {report['addressed']}.", "",
                 f"Observed PR outcomes: {json.dumps(report['outcome_counts'], sort_keys=True)}.", ""]
        for record in report["records"]:
            lines += [f"## Task {record['task']} / {record['record_id'][:12]}", "",
                      f"Source PR: {record['pr']['url']}", "",
                      f"Categories: {', '.join(record['categories'])}. "
                      f"PR outcome: {record['last_pr_state']}. "
                      f"Locally addressed: {record['addressed']}.", "",
                      f"Memory status: {record['status']}; expires at epoch {record['expires_at']}.", "",
                      "Analysis status: hypothesis requiring validation.", ""]
            for item in record["analysis"]:
                lines += [f"- Hypothesis: {item['hypothesis']}",
                          f"- Prevention: {item['prevention']}"]
            lines += ["", "Original feedback and detection context: see feedback.json.", ""]
        atomic_write(md_path, "\n".join(lines).encode())
        return {"records": len(report["records"]), "outcomes": len(report["outcomes"]),
                "json": str(json_path), "markdown": str(md_path)}

    def export_best_effort(self):
        try:
            self.export()
        except OSError as exc:
            # Durable SQLite evidence survives mirror failures, as does publication recovery.
            self.store.event(None, "learning_export_failed", {"reason": str(exc)})
