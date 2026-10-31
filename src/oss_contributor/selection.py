"""Explainable cheap prioritization, never a substitute for full issue eligibility."""

from __future__ import annotations

import re
import time
from datetime import datetime

from .store import Store


class Selector:
    def __init__(self, store: Store):
        self.store = store

    def evaluate(self, task: dict, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        evidence = task["data"].get("discovery", task["data"].get("issue", {}))
        text = f"{evidence.get('title', '')}\n{evidence.get('body', '')}"
        labels = {label.lower() for label in evidence.get("labels", [])}
        signals: list[dict] = []
        deferred = []
        retry_at = None
        if evidence.get("assignees"):
            deferred.append("search snapshot has an assignee; refresh before considering work")
        created = evidence.get("created_at")
        if created:
            try:
                date = datetime.fromisoformat(created.replace("Z", "+00:00"))
                if date.tzinfo is None:
                    raise ValueError("missing timezone")
                ready = date.timestamp() + self.store.config.discovery.min_issue_age_hours * 3600
                if ready > now:
                    deferred.append("allow maintainer triage time before automatic work")
                    retry_at = ready
            except (ValueError, TypeError, OverflowError):
                deferred.append("invalid issue creation timestamp; refresh discovery evidence")

        def add(condition: bool, points: int, reason: str):
            if condition:
                signals.append({"points": points, "reason": reason})

        add(bool(labels & {"bug", "type: bug", "kind/bug"}), 20, "bug label")
        add(bool(labels & {"good first issue", "help wanted"}), 5, "maintainer invitation label")
        add("```" in text, 15, "code/reproducer block present; correctness not yet verified")
        add(bool(re.search(r"\b(reproduc\w*|steps to reproduce|minimal example)\b", text, re.I)),
            10, "reproduction language")
        add(bool(re.search(r"\bexpected\b", text, re.I)) and
            bool(re.search(r"\b(actual|instead|returns?)\b", text, re.I)),
            10, "expected and observed behavior described")
        add(bool(re.search(r"Traceback|AssertionError|TypeError|ValueError", text)),
            5, "concrete error signal")
        add(bool(re.search(r"\b(redesign|breaking change|new feature|feature request|rewrite)\b", text, re.I)),
            -25, "possible design/feature scope; requires conservative assessment")
        # At most a small ordering bonus; closure alone is never a negative training label.
        rows = self.store.db.execute("SELECT data FROM pr_outcomes").fetchall()
        import json

        history = [json.loads(row[0]) for row in rows]
        merged = sum(r["repo"].lower() == task["repo"].lower() and r["state"] == "merged"
                     for r in history)
        add(merged > 0, min(6, merged * 2), "previous observed merges in this repository (capped bonus)")
        return {
            "score": sum(s["points"] for s in signals), "signals": signals,
            "deferred": deferred, "retry_at": retry_at, "ready": not deferred,
            "notice": "Ordering heuristic, not a merge probability. Full policy, ownership, "
                      "reproduction and independent validation remain mandatory.",
        }

    def queue(self, *, persist: bool = False) -> list[dict]:
        result = []
        now = time.time()
        for task in self.store.tasks(("queued",)):
            selection = self.evaluate(task, now)
            if persist and task["data"].get("selection") != selection:
                self.store.update(task["id"], selection=selection)
            result.append({**task, "selection": selection})
        return sorted(result, key=lambda t: (
            not t["selection"]["ready"], -t["selection"]["score"], t["id"],
        ))
