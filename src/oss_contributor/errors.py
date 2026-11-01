"""Controller-owned error categories; untrusted prose never grants retry authority."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class ErrorKind(StrEnum):
    ARGUMENT = "invalid_arguments"
    PERMISSION = "permission"
    QUOTA = "quota"
    RATE_LIMIT = "rate_limit"
    TRANSIENT = "transient"
    TIMEOUT = "timeout"
    COMMAND = "command_failed"
    VALIDATION = "validation"
    CONFLICT = "conflict"
    ENVIRONMENT = "environment"
    PROTOCOL = "protocol"
    REJECTED = "remote_rejected"
    CANCELLED = "cancelled"
    LIMIT = "execution_limit"
    RETRIES = "retry_exhausted"
    UNKNOWN = "unknown"


class ErrorInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: ErrorKind
    action: str
    retry_after_seconds: int | None = None


class ContributorError(RuntimeError):
    kind = ErrorKind.UNKNOWN
    action = "attention"


class FatalOperationError(ContributorError):
    """Must escape model tool-error responses; do not let a model replay it."""


class InvalidArguments(ContributorError):
    kind = ErrorKind.ARGUMENT
    action = "correct_input"


class PermissionDenied(FatalOperationError):
    kind = ErrorKind.PERMISSION
    action = "stop"


class AuthenticationBlocked(PermissionDenied):
    action = "pause"


class QuotaExhausted(FatalOperationError):
    kind = ErrorKind.QUOTA
    action = "pause"


class RateLimited(FatalOperationError):
    kind = ErrorKind.RATE_LIMIT
    action = "wait"

    def __init__(self, seconds: int = 600):
        self.seconds = max(0, seconds)
        super().__init__(f"GitHub rate limited; retry after {self.seconds} seconds")


class TransientFailure(ContributorError):
    kind = ErrorKind.TRANSIENT
    action = "retry_safe_read"


class CommandTimeout(ContributorError):
    kind = ErrorKind.TIMEOUT
    action = "attention"


class ValidationFailure(FatalOperationError):
    kind = ErrorKind.VALIDATION

    def __init__(self, message: str = "", *, cause_info: ErrorInfo | None = None):
        if cause_info is not None:
            self.kind = cause_info.kind
        super().__init__(message)


class CommandFailed(ContributorError):
    kind = ErrorKind.COMMAND
    action = "inspect_result"


class PublicationConflict(FatalOperationError):
    kind = ErrorKind.CONFLICT


class EnvironmentFailure(FatalOperationError):
    kind = ErrorKind.ENVIRONMENT


class ProtocolFailure(FatalOperationError):
    kind = ErrorKind.PROTOCOL


class Cancelled(FatalOperationError):
    kind = ErrorKind.CANCELLED
    action = "stop"


class RetryExhausted(FatalOperationError):
    kind = ErrorKind.RETRIES


class OutcomeUncertain(FatalOperationError):
    """A possibly mutating call failed; inspect effects rather than replaying it."""


class RemoteRejected(ContributorError):
    kind = ErrorKind.REJECTED

    def __init__(self, status: int):
        self.status = status
        super().__init__(f"GitHub request rejected (HTTP {status})")


def classify_error(error: BaseException) -> ErrorInfo:
    if isinstance(error, ContributorError):
        return ErrorInfo(kind=error.kind, action=error.action,
                         retry_after_seconds=error.seconds if isinstance(error, RateLimited) else None)
    if isinstance(error, PermissionError):
        return ErrorInfo(kind=ErrorKind.PERMISSION, action="stop")
    if isinstance(error, FileNotFoundError):
        return ErrorInfo(kind=ErrorKind.ENVIRONMENT, action="attention")
    if isinstance(error, (TimeoutError,)):
        return ErrorInfo(kind=ErrorKind.TIMEOUT, action="attention")
    if isinstance(error, ConnectionError):
        return ErrorInfo(kind=ErrorKind.TRANSIENT, action="retry_safe_read")
    if isinstance(error, (ValueError, TypeError)):
        return ErrorInfo(kind=ErrorKind.ARGUMENT, action="correct_input")
    return ErrorInfo(kind=ErrorKind.UNKNOWN, action="attention")


def publication_recoverable(error: BaseException) -> bool:
    # Unknown external effects still need the existing bounded remote reconciliation,
    # not blind wire retries. Known permanent failures require attention instead.
    return classify_error(error).kind in {
        ErrorKind.TRANSIENT, ErrorKind.TIMEOUT, ErrorKind.UNKNOWN, ErrorKind.RETRIES,
    }
