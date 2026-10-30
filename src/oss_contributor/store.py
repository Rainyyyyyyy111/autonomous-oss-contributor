from __future__ import annotations

import fcntl
import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .config import Config
from .models import Issue
from .process import ContributorError


class Store:
    def __init__(self, config: Config):
        self.config = config
        self.home = config.state_dir.expanduser().resolve()
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.home / "state.sqlite3", timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
          PRAGMA journal_mode=WAL;
          CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS candidates(repo TEXT PRIMARY KEY, data TEXT, discovered REAL);
          CREATE TABLE IF NOT EXISTS tasks(
            id INTEGER PRIMARY KEY, repo TEXT NOT NULL, issue INTEGER NOT NULL,
            status TEXT NOT NULL, data TEXT NOT NULL, updated REAL NOT NULL,
            UNIQUE(repo,issue));
          CREATE TABLE IF NOT EXISTS usage(
            day TEXT PRIMARY KEY, attempts INTEGER NOT NULL DEFAULT 0,
            publications INTEGER NOT NULL DEFAULT 0, model_seconds REAL NOT NULL DEFAULT 0);
          CREATE TABLE IF NOT EXISTS events(
            id INTEGER PRIMARY KEY, task INTEGER, name TEXT, data TEXT, created REAL);
          CREATE TABLE IF NOT EXISTS processed(task INTEGER, event TEXT, PRIMARY KEY(task,event));
          CREATE TABLE IF NOT EXISTS desktop_jobs(
            ticket TEXT PRIMARY KEY, task INTEGER NOT NULL, state TEXT NOT NULL,
            data TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS notifications(
            id INTEGER PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL,
            data TEXT NOT NULL, acknowledged INTEGER NOT NULL DEFAULT 0);
        """)

    def close(self):
        self.db.close()

    def day(self) -> str:
        return datetime.now(ZoneInfo(self.config.timezone)).date().isoformat()

    @contextmanager
    def lock(self):
        with (self.home / "worker.lock").open("a+") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ContributorError("another worker is running") from None
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def meta(self, key: str, default: str = "") -> str:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value: str):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, value))

    def event(self, task: int | None, name: str, data: Any):
        now = time.time()
        encoded = json.dumps(data, ensure_ascii=False, default=str)
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO events(task,name,data,created) VALUES(?,?,?,?)",
                (task, name, encoded, now),
            )
        # SQLite is authoritative. JSONL is an inspectable mirror, not a recovery journal.
        try:
            with (self.home / "events.jsonl").open("a") as stream:
                stream.write(
                    json.dumps(
                        {
                            "id": cursor.lastrowid,
                            "task": task,
                            "name": name,
                            "data": data,
                            "created": now,
                        },
                        default=str,
                    )
                    + "\n"
                )
        except OSError:
            # A mirror failure must not turn an already committed publication into a failed task.
            pass

    def candidate(self, repo: str, data: dict):
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO candidates VALUES(?,?,?)",
                (repo, json.dumps(data), time.time()),
            )

    def create_task(self, issue: Issue) -> int | None:
        with self.db:
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO tasks(repo,issue,status,data,updated) VALUES(?,?,?,?,?)",
                (
                    issue.repo.lower(),
                    issue.number,
                    "queued",
                    json.dumps({"issue": issue.model_dump()}),
                    time.time(),
                ),
            )
            return cursor.lastrowid if cursor.rowcount else None

    def get(self, task_id: int) -> dict:
        row = self.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise ContributorError(f"unknown task {task_id}")
        return {**dict(row), "data": json.loads(row["data"])}

    def tasks(self, statuses: tuple[str, ...] | None = None) -> list[dict]:
        query = "SELECT id FROM tasks"
        args: tuple = ()
        if statuses:
            query += " WHERE status IN (" + ",".join("?" for _ in statuses) + ")"
            args = statuses
        return [
            self.get(row[0]) for row in self.db.execute(query + " ORDER BY id", args).fetchall()
        ]

    def update(self, task_id: int, status: str | None = None, **data):
        task = self.get(task_id)
        task["data"].update(data)
        with self.db:
            self.db.execute(
                "UPDATE tasks SET status=?,data=?,updated=? WHERE id=?",
                (status or task["status"], json.dumps(task["data"]), time.time(), task_id),
            )
        self.event(task_id, status or task["status"], data)

    def usage(self) -> dict:
        row = self.db.execute("SELECT * FROM usage WHERE day=?", (self.day(),)).fetchone()
        return (
            dict(row)
            if row
            else {"day": self.day(), "attempts": 0, "publications": 0, "model_seconds": 0}
        )

    def reserve(self, kind: str, amount: float = 1) -> bool:
        caps = {
            "attempts": self.config.limits.attempts_per_day,
            "publications": self.config.limits.prs_per_day,
            "model_seconds": self.config.limits.model_seconds_per_day,
        }
        if kind not in caps:
            raise ValueError(kind)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO usage(day) VALUES(?)", (self.day(),))
            cur = self.db.execute(
                f"UPDATE usage SET {kind}={kind}+? WHERE day=? AND {kind}+?<=?",
                (amount, self.day(), amount, caps[kind]),
            )
            return bool(cur.rowcount)

    def refund_model(self, day: str, seconds: float):
        with self.db:
            self.db.execute(
                "UPDATE usage SET model_seconds=MAX(0,model_seconds-?) WHERE day=?", (seconds, day)
            )

    def seen(self, task: int, event: str) -> bool:
        return (
            self.db.execute(
                "SELECT 1 FROM processed WHERE task=? AND event=?", (task, event)
            ).fetchone()
            is not None
        )

    def mark_seen(self, task: int, events: list[str]):
        with self.db:
            self.db.executemany(
                "INSERT OR IGNORE INTO processed VALUES(?,?)", [(task, event) for event in events]
            )

    def desktop_jobs(self, active_only: bool = True) -> list[dict]:
        jobs = [
            {**dict(row), "data": json.loads(row["data"])}
            for row in self.db.execute("SELECT * FROM desktop_jobs ORDER BY created").fetchall()
        ]
        if not active_only:
            return jobs
        active = [
            job
            for job in jobs
            if job["state"] in {"dispatching", "attached", "validating", "cancelling"}
        ]
        if active:
            return active
        # 0.2 failed leases did not persist native termination. Reconcile one at a time.
        return [
            job
            for job in jobs
            if job["state"] == "failed" and not job["data"].get("thread_stopped")
        ][:1]

    def desktop_job(self, ticket: str) -> dict:
        row = self.db.execute("SELECT * FROM desktop_jobs WHERE ticket=?", (ticket,)).fetchone()
        if row is None:
            raise ContributorError("unknown desktop job ticket")
        return {**dict(row), "data": json.loads(row["data"])}

    def save_desktop_job(self, job: dict):
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO desktop_jobs VALUES(?,?,?,?,?,?)",
                (
                    job["ticket"],
                    job["task"],
                    job["state"],
                    json.dumps(job["data"]),
                    job["created"],
                    job["expires"],
                ),
            )

    def notify(self, fingerprint: str, **data):
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO notifications(fingerprint,data) VALUES(?,?)",
                (fingerprint, json.dumps(data)),
            )

    def notifications(self, include_acknowledged: bool = False) -> list[dict]:
        query = "SELECT id,data FROM notifications"
        if not include_acknowledged:
            query += " WHERE acknowledged=0"
        return [
            {"id": row["id"], **json.loads(row["data"])}
            for row in self.db.execute(query + " ORDER BY id").fetchall()
        ]

    def acknowledge(self, ids: list[int]):
        with self.db:
            self.db.executemany(
                "UPDATE notifications SET acknowledged=1 WHERE id=?", [(i,) for i in ids]
            )
