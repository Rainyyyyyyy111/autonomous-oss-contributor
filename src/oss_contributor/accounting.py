"""Observed usage and immutable call provenance; absence of telemetry is not zero."""

from __future__ import annotations

import hashlib
import json
import time
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import __version__
from .errors import ProtocolFailure

if TYPE_CHECKING:
    from .store import Store

PROMPT_REVISION = "oss-controller-prompts-v1"
Counter = Annotated[int, Field(strict=True, ge=0)]


class Prices(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_usd_per_million: Decimal = Field(ge=0, le=100000)
    cached_input_usd_per_million: Decimal = Field(ge=0, le=100000)
    output_usd_per_million: Decimal = Field(ge=0, le=100000)


class MeteringPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prices: dict[str, Prices] = Field(default_factory=dict)
    max_observed_tokens_per_call: int | None = Field(None, ge=1)
    max_estimated_usd_per_call: Decimal | None = Field(None, gt=0, le=100000)


class UsageObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1, max_length=200)
    thread_id: str = Field(min_length=1, max_length=200)
    input_tokens: Counter
    cached_input_tokens: Counter
    output_tokens: Counter
    reasoning_output_tokens: Counter = 0
    model: str | None = Field(None, min_length=1, max_length=200)
    final: bool = Field(False, strict=True)

    @model_validator(mode="after")
    def valid_subsets(self):
        if self.cached_input_tokens > self.input_tokens or self.reasoning_output_tokens > self.output_tokens:
            raise ValueError("cached/reasoning counts must be subsets of input/output")
        return self

    def counts(self) -> dict:
        return self.model_dump(include={"input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens"})


def digest(value) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode()).hexdigest()


def implementation_digest(root: Path | None = None) -> str:
    root = root or Path(__file__).parent
    files = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(root.rglob("*.py")) if path.is_file() and not path.is_symlink()}
    return digest(files)


def provenance(backend: str, prompt: str | None, *, requested_model: str | None = None,
               instructions: str | None = None, tools=None, schema=None, stage: str = "repair",
               protocol_version: str | None = None) -> dict:
    return {
        "backend": backend, "stage": stage, "package_version": __version__,
        "prompt_revision": PROMPT_REVISION, "prompt_sha256": digest(prompt) if prompt is not None else None,
        "instructions_sha256": digest(instructions) if instructions is not None else None,
        "tools_sha256": digest(tools) if tools is not None else None,
        "schema_sha256": digest(schema) if schema is not None else None,
        "implementation_sha256": implementation_digest(), "protocol_version": protocol_version,
        "requested_model": requested_model, "observed_model": None,
        "model_identity_status": "unknown",  # No immutable model-weight identity is available.
    }


def finalization_reserve(total: float, configured: float) -> float:
    """Reserve within, never beyond, the existing attempt deadline."""
    return min(configured, max(0, total) / 3)


class Accounting:
    def __init__(self, store: Store):
        self.store = store

    def start(self, call_id: str, task: int, trace: dict, policy: MeteringPolicy, *, prepared=False, day: str | None = None):
        data = {"trace": trace, "policy": policy.model_dump(mode="json"), "usage": None,
                "coverage": "unknown", "limit_reached": False}
        with self.store.db:
            self.store.db.execute(
                "INSERT OR IGNORE INTO model_calls VALUES(?,?,?,?,?,?,?)",
                (call_id, task, day if day is not None else self.store.day(), "prepared" if prepared else "running",
                 json.dumps(data), time.time(), None),
            )

    def get(self, call_id: str) -> dict:
        row = self.store.db.execute("SELECT * FROM model_calls WHERE id=?", (call_id,)).fetchone()
        if row is None:
            raise ProtocolFailure("unknown model accounting call")
        return {**dict(row), "data": json.loads(row["data"])}

    def bind(self, call_id: str, thread_id: str, model: str | None = None, *, requested_model=None, provider=None):
        call = self.get(call_id)
        data = call["data"]
        if data.get("thread_id") not in {None, thread_id}:
            raise ProtocolFailure("accounting thread identity changed")
        data["thread_id"] = thread_id
        data["trace"]["requested_model"] = requested_model
        if isinstance(provider, str):
            data["trace"]["provider"] = provider
        if model is not None:
            old = data["trace"]["observed_model"]
            data["trace"]["observed_model"] = model if old in {None, model} else "mixed"
            data["trace"]["model_identity_status"] = "reported_name"
        self._save(call, data, status="running" if call["status"] == "prepared" else call["status"])

    def _save(self, call: dict, data: dict, *, status: str | None = None):
        with self.store.db:
            self.store.db.execute("UPDATE model_calls SET data=?,status=? WHERE id=?",
                                  (json.dumps(data), status or call["status"], call["id"]))

    @staticmethod
    def estimate(data: dict) -> Decimal | None:
        usage = data.get("usage")
        model = data["trace"]["observed_model"]
        price = data["policy"]["prices"].get(model) if model and model != "mixed" else None
        if usage is None or price is None:
            return None
        return (
            Decimal(usage["input_tokens"] - usage["cached_input_tokens"]) * Decimal(price["input_usd_per_million"])
            + Decimal(usage["cached_input_tokens"]) * Decimal(price["cached_input_usd_per_million"])
            + Decimal(usage["output_tokens"]) * Decimal(price["output_usd_per_million"])
        ) / Decimal(1000000)

    def constraint_failure(self, call_id: str, *, require_final: bool = False) -> str | None:
        data = self.get(call_id)["data"]
        policy = MeteringPolicy.model_validate(data["policy"])
        if data["limit_reached"]:
            return "observed model usage limit reached"
        if require_final and (policy.max_observed_tokens_per_call is not None or policy.max_estimated_usd_per_call is not None):
            if data["coverage"] != "reported" or data["usage"] is None:
                return "model usage unavailable to verify configured limit"
            if policy.max_estimated_usd_per_call is not None and self.estimate(data) is None:
                return "model price unavailable to verify configured cost limit"
        return None

    def observe(self, call_id: str, observation: UsageObservation) -> dict:
        call = self.get(call_id)
        data = call["data"]
        if data.get("thread_id") != observation.thread_id:
            raise ProtocolFailure("usage observation belongs to another thread")
        encoded = digest(observation.model_dump(mode="json"))
        old_event = self.store.db.execute(
            "SELECT digest FROM model_usage_events WHERE call_id=? AND event_id=?",
            (call_id, observation.event_id),
        ).fetchone()
        if old_event:
            if old_event[0] != encoded:
                raise ProtocolFailure("usage event identity reused with different counters")
            return data
        if self.store.db.execute("SELECT COUNT(*) FROM model_usage_events WHERE call_id=?", (call_id,)).fetchone()[0] >= 4096:
            raise ProtocolFailure("usage observation event limit reached")
        previous, current = data.get("usage"), observation.counts()
        if previous is not None:
            lower = any(current[key] < previous[key] for key in current)
            higher = any(current[key] > previous[key] for key in current)
            if lower and higher:
                raise ProtocolFailure("inconsistent cumulative usage counters")
            if lower:
                with self.store.db:
                    self.store.db.execute("INSERT INTO model_usage_events VALUES(?,?,?)", (call_id, observation.event_id, encoded))
                return data  # An older snapshot cannot lower charges or mark usage final.
        if observation.model is not None:
            old_model = data["trace"]["observed_model"]
            data["trace"]["observed_model"] = observation.model if old_model in {None, observation.model} else "mixed"
            data["trace"]["model_identity_status"] = "reported_name"
        data["usage"] = current
        data["coverage"] = "reported" if observation.final or (
            previous == current and data["coverage"] == "reported"
        ) else "partial"
        policy = MeteringPolicy.model_validate(data["policy"])
        cost = self.estimate(data)
        data["limit_reached"] = data["limit_reached"] or bool(
            policy.max_observed_tokens_per_call is not None
            and current["input_tokens"] + current["output_tokens"] >= policy.max_observed_tokens_per_call
            or policy.max_estimated_usd_per_call is not None
            and cost is not None and cost >= policy.max_estimated_usd_per_call
        )
        # Observation and dedup key commit atomically. No raw chat/tool data is stored.
        with self.store.db:
            self.store.db.execute("INSERT INTO model_usage_events VALUES(?,?,?)", (call_id, observation.event_id, encoded))
            self.store.db.execute("UPDATE model_calls SET data=? WHERE id=?", (json.dumps(data), call_id))
        return data

    def finish(self, call_id: str, status: str):
        self.get(call_id)
        with self.store.db:
            self.store.db.execute("UPDATE model_calls SET status=?,finished=COALESCE(finished,?) WHERE id=? AND finished IS NULL",
                                  (status, time.time(), call_id))

    def report(self, task_ids: set[int] | None = None) -> dict:
        rows = self.store.db.execute("SELECT id FROM model_calls ORDER BY created,id").fetchall()
        calls = [self.get(row[0]) for row in rows]
        calls = [call for call in calls if task_ids is None or call["task"] in task_ids]
        for call in calls:
            cost = self.estimate(call["data"])
            call["estimated_usd"] = str(cost) if cost is not None else None
        return {
            "calls": calls, "observed_input_tokens": sum(c["data"]["usage"]["input_tokens"] for c in calls if c["data"]["usage"]),
            "observed_output_tokens": sum(c["data"]["usage"]["output_tokens"] for c in calls if c["data"]["usage"]),
            "unmetered_calls": sum(c["data"]["usage"] is None for c in calls),
            "partial_calls": sum(c["data"]["coverage"] == "partial" for c in calls),
            "unpriced_calls": sum(c["estimated_usd"] is None for c in calls),
            "known_estimated_usd": str(sum((Decimal(c["estimated_usd"]) for c in calls if c["estimated_usd"] is not None), Decimal(0))),
            "estimated_usd": None if any(c["estimated_usd"] is None or c["data"]["coverage"] != "reported" for c in calls)
            else str(sum((Decimal(c["estimated_usd"]) for c in calls), Decimal(0))),
            "note": "Observed cumulative thread counters; missing usage/prices are unknown, not zero. "
                    "USD costs use pinned operator-supplied rates and are estimates, not invoices. "
                    "Observed limits can overshoot before telemetry arrives. Calls group by reservation day, not billing day.",
        }
