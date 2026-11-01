"""One retry owner per safe read, bounded by attempts, elapsed time and a shared budget."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from .errors import (
    Cancelled,
    CommandTimeout,
    ErrorKind,
    FatalOperationError,
    OutcomeUncertain,
    RetryExhausted,
    classify_error,
)


class RetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_attempts: int = Field(3, ge=1, le=5)
    max_retries: int = Field(6, ge=0, le=20)
    max_elapsed_seconds: float = Field(15, gt=0, le=30)
    attempt_timeout_seconds: float = Field(5, gt=0, le=30)
    base_delay_seconds: float = Field(0.5, ge=0, le=5)
    max_delay_seconds: float = Field(2, ge=0, le=5)


@dataclass
class RetryBudget:
    policy: RetryPolicy
    used: int = 0

    def reserve(self) -> bool:
        if self.used >= self.policy.max_retries:
            return False
        self.used += 1
        return True


def retry_read[T](operation: Callable[[float | None], T], *, safe: bool, policy: RetryPolicy,
               reserve: Callable[[], bool], deadline: float | None = None,
               check: Callable = lambda: None, on_error: Callable = lambda info: None,
               clock: Callable = time.monotonic, sleep: Callable = time.sleep,
               jitter: Callable = random.random) -> T:
    # Non-idempotent operations keep their original deadline and run exactly once.
    end = min(deadline if deadline is not None else float("inf"),
              clock() + policy.max_elapsed_seconds) if safe else deadline

    def exhausted(reason, cause):
        error = RetryExhausted(reason)
        on_error(classify_error(error))
        raise error from cause

    for attempt in range(policy.max_attempts):
        check()
        if end is not None and clock() >= end:
            error = Cancelled("operation deadline exceeded")
            on_error(classify_error(error))
            raise error
        try:
            attempt_end = min(end, clock() + policy.attempt_timeout_seconds) if safe else end
            result = operation(attempt_end)
            if safe and attempt_end is not None and clock() > attempt_end:
                raise CommandTimeout("safe-read attempt deadline exceeded")
            return result
        except Exception as exc:
            info = classify_error(exc)
            on_error(info)
            if isinstance(exc, FatalOperationError):
                raise
            if not safe and info.kind in {ErrorKind.TRANSIENT, ErrorKind.TIMEOUT}:
                uncertain = OutcomeUncertain("operation failed with uncertain effects; do not replay")
                on_error(classify_error(uncertain))
                raise uncertain from exc
            if not safe or info.kind not in {ErrorKind.TRANSIENT, ErrorKind.TIMEOUT}:
                raise
            if attempt + 1 >= policy.max_attempts:
                exhausted("safe-read retry attempt limit reached", exc)
            delay = min(policy.max_delay_seconds, policy.base_delay_seconds * 2 ** attempt)
            delay *= max(0.0, min(1.0, jitter()))
            check()
            if end is not None and clock() + delay >= end:
                exhausted("safe-read retry time window exhausted", exc)
            if not reserve():
                exhausted("safe-read shared retry budget exhausted", exc)
            sleep(delay)
    raise AssertionError("unreachable retry state")
