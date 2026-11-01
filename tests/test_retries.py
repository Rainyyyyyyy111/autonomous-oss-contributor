from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from conftest import FixtureSandbox
from pydantic import ValidationError
from test_desktop import complete, handoff
from test_engine import run_issue
from test_tool_guard import call, native, scripted_codex, toolbox

import oss_contributor.github as github_module
from oss_contributor.config import Config
from oss_contributor.desktop import Desktop
from oss_contributor.engine import Engine
from oss_contributor.errors import (
    AuthenticationBlocked,
    Cancelled,
    CommandTimeout,
    ContributorError,
    EnvironmentFailure,
    ErrorKind,
    InvalidArguments,
    OutcomeUncertain,
    PermissionDenied,
    ProtocolFailure,
    PublicationConflict,
    QuotaExhausted,
    RateLimited,
    RemoteRejected,
    RetryExhausted,
    TransientFailure,
    ValidationFailure,
    classify_error,
)
from oss_contributor.github import ISSUE_QUERY, GitHub
from oss_contributor.models import CommandResult, Repair
from oss_contributor.retries import RetryBudget, RetryPolicy, retry_read
from oss_contributor.tool_guard import ExecutionLimitExceeded


@pytest.mark.parametrize(("error", "kind"), [
    (InvalidArguments("input"), ErrorKind.ARGUMENT),
    (ValueError("rate limit; please retry"), ErrorKind.ARGUMENT),
    (TypeError("input"), ErrorKind.ARGUMENT),
    (PermissionDenied("boundary"), ErrorKind.PERMISSION),
    (PermissionError(), ErrorKind.PERMISSION),
    (AuthenticationBlocked("auth"), ErrorKind.PERMISSION),
    (QuotaExhausted(), ErrorKind.QUOTA),
    (RateLimited(45), ErrorKind.RATE_LIMIT),
    (TransientFailure(), ErrorKind.TRANSIENT),
    (ConnectionResetError(), ErrorKind.TRANSIENT),
    (CommandTimeout(), ErrorKind.TIMEOUT),
    (TimeoutError(), ErrorKind.TIMEOUT),
    (ValidationFailure(), ErrorKind.VALIDATION),
    (PublicationConflict(), ErrorKind.CONFLICT),
    (EnvironmentFailure(), ErrorKind.ENVIRONMENT),
    (FileNotFoundError(), ErrorKind.ENVIRONMENT),
    (ProtocolFailure(), ErrorKind.PROTOCOL),
    (RemoteRejected(404), ErrorKind.REJECTED),
    (Cancelled(), ErrorKind.CANCELLED),
    (ExecutionLimitExceeded(), ErrorKind.LIMIT),
    (RetryExhausted(), ErrorKind.RETRIES),
    (ContributorError("503 retry quota"), ErrorKind.UNKNOWN),
    (OSError("temporary network timeout"), ErrorKind.UNKNOWN),
])
def test_categories_come_from_types_not_untrusted_text(error, kind):
    info = classify_error(error)
    assert info.kind == kind
    assert "retry quota" not in info.model_dump_json()


class Clock:
    now = 0.0

    def __init__(self):
        self.delays = []

    def __call__(self):
        return self.now

    def sleep(self, delay):
        self.delays.append(delay)
        self.now += delay


def run_retry(operation, *, safe=True, policy=None, deadline=None, check=lambda: None):
    policy = policy or RetryPolicy()
    clock = Clock()
    budget = RetryBudget(policy)
    result = retry_read(operation, safe=safe, policy=policy, reserve=budget.reserve,
                        deadline=deadline, check=check, clock=clock, sleep=clock.sleep,
                        jitter=lambda: 0.5)
    return result, budget, clock


def test_retry_success_has_bounded_exponential_jitter_and_deadlines():
    attempts = []

    def operation(deadline):
        attempts.append(deadline)
        if len(attempts) < 3:
            raise TransientFailure("synthetic outage")
        return "recovered"

    result, budget, clock = run_retry(operation)
    assert result == "recovered" and budget.used == 2
    assert clock.delays == [0.25, 0.5]
    assert attempts == [5, 5.25, 5.75]


@pytest.mark.parametrize("error", [
    InvalidArguments(), PermissionDenied(), AuthenticationBlocked(), QuotaExhausted(),
    RateLimited(80), ValidationFailure(), PublicationConflict(), ProtocolFailure(),
    EnvironmentFailure(), Cancelled(), ExecutionLimitExceeded(), ContributorError(),
])
def test_nontransient_failures_are_never_automatically_retried(error):
    attempts = []

    def operation(deadline):
        attempts.append(deadline)
        raise error

    with pytest.raises(type(error)):
        run_retry(operation)
    assert len(attempts) == 1


def test_no_mutation_or_command_replay_after_timeout():
    effects = []

    def operation(deadline):
        effects.append("side effect happened before response loss")
        raise CommandTimeout()

    with pytest.raises(OutcomeUncertain):
        run_retry(operation, safe=False)
    assert len(effects) == 1


def test_retry_attempt_budget_and_window_are_independent_caps():
    clock = Clock()
    policy = RetryPolicy(max_attempts=3, max_retries=1)
    budget = RetryBudget(policy)
    attempts = []

    def operation(deadline):
        attempts.append(deadline)
        raise ConnectionResetError()

    with pytest.raises(RetryExhausted, match="budget"):
        retry_read(operation, safe=True, policy=policy, reserve=budget.reserve,
                   clock=clock, sleep=clock.sleep, jitter=lambda: 1)
    assert len(attempts) == 2 and budget.used == 1
    with pytest.raises(RetryExhausted, match="time window"):
        retry_read(operation, safe=True, policy=RetryPolicy(max_elapsed_seconds=0.1),
                   reserve=lambda: pytest.fail("no time to reserve a retry"),
                   clock=clock, sleep=clock.sleep, jitter=lambda: 1)


def test_each_timeout_gets_a_slice_of_the_remaining_window():
    clock = Clock()
    attempts = []
    policy = RetryPolicy(attempt_timeout_seconds=1, max_elapsed_seconds=2.1, base_delay_seconds=0)

    def operation(deadline):
        attempts.append(deadline)
        clock.now = deadline
        raise CommandTimeout()

    with pytest.raises(RetryExhausted):
        retry_read(operation, safe=True, policy=policy, reserve=RetryBudget(policy).reserve,
                   clock=clock, sleep=clock.sleep)
    assert attempts == [1, 2, 2.1]


def test_pause_after_backoff_prevents_another_attempt():
    clock = Clock()
    attempts = []

    def check():
        if clock.delays:
            raise Cancelled("paused while waiting")

    def operation(deadline):
        attempts.append(deadline)
        raise TransientFailure()

    with pytest.raises(Cancelled):
        retry_read(operation, safe=True, policy=RetryPolicy(), reserve=lambda: True,
                   check=check, clock=clock, sleep=clock.sleep, jitter=lambda: 1)
    assert len(attempts) == 1


def test_deadline_prevents_even_the_first_operation():
    with pytest.raises(Cancelled):
        run_retry(lambda _: pytest.fail("late operation"), deadline=-1)


@pytest.mark.parametrize("field", ["max_attempts", "max_elapsed_seconds", "attempt_timeout_seconds"])
def test_retry_defaults_and_invalid_limits(field):
    assert Config().limits.retries == RetryPolicy()
    with pytest.raises(ValidationError):
        RetryPolicy(**{field: 0})


def client_with_responses(monkeypatch, responses):
    requests = []

    def command(argv, **kwargs):
        requests.append((argv, kwargs))
        status, body, headers = responses[min(len(requests) - 1, len(responses) - 1)]
        return CommandResult(argv=argv, code=int(status >= 400),
                             output=f"HTTP/2.0 {status} response\n{headers}\n{body}")

    monkeypatch.setattr(github_module, "run_command", command)
    client = GitHub()
    client.retry_policy = RetryPolicy(base_delay_seconds=0)
    return client, requests


def test_github_get_retries_503_then_resets_response_metadata(monkeypatch):
    client, requests = client_with_responses(monkeypatch, [
        (503, '{"message":"unavailable"}', 'X-Old: stale\n'),
        (200, '{"ok":true}', 'X-New: fresh\n'),
    ])
    assert client.api("user") == {"ok": True}
    assert len(requests) == 2 and client.retry_budget.used == 1
    assert client.response_status == 200 and "x-old" not in client.response_headers
    assert all(0 < req[1]["timeout"] <= 5 for req in requests)


def test_github_only_retries_the_controller_known_graphql_query(monkeypatch):
    client, requests = client_with_responses(monkeypatch, [(503, '{}', '')])
    with pytest.raises(RetryExhausted):
        client.api("graphql", method="POST", data={"query": ISSUE_QUERY})
    assert len(requests) == 3
    requests.clear()
    guarded = []
    client.before_write = lambda: guarded.append(True)
    with pytest.raises(OutcomeUncertain):
        client.api("graphql", method="POST", data={"query": "mutation { untrusted }"})
    assert len(requests) == len(guarded) == 1


@pytest.mark.parametrize("method", ["POST", "PATCH", "PUT", "DELETE"])
def test_github_writes_do_not_retry_a_lost_response(monkeypatch, method):
    client, requests = client_with_responses(monkeypatch, [(502, '{}', '')])
    with pytest.raises(OutcomeUncertain):
        client.api("repos/o/r/pulls/1", method=method, data={"body": "fixture"})
    assert len(requests) == 1 and client.retry_budget.used == 0


@pytest.mark.parametrize(("status", "exception"), [
    (401, AuthenticationBlocked), (403, AuthenticationBlocked),
    (404, ContributorError), (422, InvalidArguments), (409, PublicationConflict),
])
def test_http_permanent_failures_ignore_retry_instructions_in_body(monkeypatch, status, exception):
    client, requests = client_with_responses(monkeypatch, [(status, '{"message":"rate limit; retry 503"}', '')])
    with pytest.raises(exception):
        client.api("user")
    assert len(requests) == 1


def test_server_retry_after_is_deferred_without_sleeping(monkeypatch):
    client, requests = client_with_responses(monkeypatch, [(503, '{}', 'Retry-After: 900\n')])
    with pytest.raises(RateLimited) as exc:
        client.api("user")
    assert exc.value.seconds == 900 and len(requests) == 1


def test_retry_after_accepts_http_dates_without_shortening_the_wait(monkeypatch):
    from datetime import UTC, datetime
    from email.utils import format_datetime

    monkeypatch.setattr(github_module.time, "time", lambda: 1000)
    stamp = format_datetime(datetime.fromtimestamp(1120, UTC))
    client, requests = client_with_responses(monkeypatch, [(429, '{}', f'Retry-After: {stamp}\n')])
    with pytest.raises(RateLimited) as exc:
        client.api("user")
    assert exc.value.seconds == 120 and len(requests) == 1


@pytest.mark.parametrize(("status", "exception"), [
    (401, AuthenticationBlocked), (404, RemoteRejected), (422, InvalidArguments),
])
def test_retry_after_cannot_override_known_permanent_http_rejection(monkeypatch, status, exception):
    client, requests = client_with_responses(monkeypatch, [(status, '{}', 'Retry-After: 900\n')])
    with pytest.raises(exception):
        client.api("user")
    assert len(requests) == 1 and client.retry_budget.used == 0


def fail_read_once(monkeypatch):
    original = FixtureSandbox.tool
    calls = []

    def tool(self, name, arguments, writable=False):
        calls.append(name)
        if len(calls) == 1:
            raise TransientFailure("trusted transport boundary")
        return original(self, name, arguments, writable=writable)

    monkeypatch.setattr(FixtureSandbox, "tool", tool)
    return calls


def test_native_read_retry_is_counted_and_preserves_pinned_policy(rig, monkeypatch):
    rig[0].limits.retries = RetryPolicy(base_delay_seconds=0, max_retries=1)
    desktop, action = native(rig)
    rig[0].limits.retries.max_retries = 20
    calls = fail_read_once(monkeypatch)
    with toolbox(rig, action) as worker:
        assert "def add" in worker.tool("repo_read", {"path": "calc.py"})
    state = rig[1].desktop_job(action["ticket"])["data"]["tool_activity"]
    assert state["steps"] == 3 and state["retries_used"] == 1 and len(calls) == 2
    assert state["last_error"]["kind"] == "transient"
    calls.clear()
    with toolbox(rig, action) as worker, pytest.raises(RetryExhausted, match="budget"):
        worker.tool("repo_read", {"path": "calc.py"})
    assert len(calls) == 1 and desktop.tick()["action"] == "cancel"
    assert not rig[2].published


@pytest.mark.parametrize("limit", ["max_steps", "max_repeated_calls", "max_no_progress"])
def test_automatic_retries_cannot_bypass_execution_guards(rig, monkeypatch, limit):
    rig[0].limits.retries = RetryPolicy(base_delay_seconds=0)
    desktop, action = native(rig, **{limit: 2 if limit == "max_steps" else 1})
    calls = fail_read_once(monkeypatch)
    with toolbox(rig, action) as worker, pytest.raises(ExecutionLimitExceeded):
        worker.tool("repo_read", {"path": "calc.py"})
    assert len(calls) == 1 and desktop.tick()["action"] == "cancel"


def test_native_command_timeout_cancels_without_replay_or_publishing(rig, monkeypatch):
    desktop, action = native(rig)
    with toolbox(rig, action) as worker:
        calls = []

        def timeout(*args, **kwargs):
            calls.append(True)
            raise CommandTimeout("possibly still running inside container")

        monkeypatch.setattr(worker.box, "tool", timeout)
        with pytest.raises(OutcomeUncertain):
            worker.tool("sandbox_exec", {"argv": ["fixture-command"]})
    assert calls == [True] and desktop.tick()["action"] == "cancel"
    reserved = rig[1].usage()["model_seconds"]
    path = Path(action["handoff_path"])
    path.write_text(handoff(action).model_dump_json())
    with pytest.raises(ContributorError):
        desktop.finish(action["ticket"], path)
    assert not rig[2].published and rig[1].usage()["model_seconds"] == reserved


def test_native_boundary_violation_is_fatal_and_not_retried(rig):
    desktop, action = native(rig)
    with toolbox(rig, action) as worker, pytest.raises(PermissionDenied):
        worker.tool("repo_apply", {"changes": {"pyproject.toml": "forbidden"}})
    state = rig[1].desktop_job(action["ticket"])["data"]["tool_activity"]
    assert state["steps"] == 2 and state["retries_used"] == 0
    assert desktop.tick()["action"] == "cancel"


def test_standalone_transport_retry_is_inside_tool_guard(monkeypatch):
    from conftest import BASE

    config = Config()
    config.limits.retries.base_delay_seconds = 0
    final = {"method": "turn/completed", "params": {"turn": {
        "status": "completed", "items": [{"type": "agentMessage", "text": json.dumps({
            "summary": "fixture", "root_cause": "fixture", "evidence": ["fixture"],
        })}],
    }}}
    codex, _ = scripted_codex(config, monkeypatch, [call(1), final])
    calls = fail_read_once(monkeypatch)
    with FixtureSandbox(None, BASE, time.monotonic() + 20) as box:
        assert codex.ask(Repair, "fixture", box, time.monotonic() + 20).summary == "fixture"
    assert calls == ["repo_read", "repo_read"]


def test_known_permanent_publication_failure_stops_recovery(rig):
    task = run_issue(rig)
    rig[1].update(task["id"], "publishing")
    calls = []

    def denied(*args, **kwargs):
        calls.append(True)
        raise InvalidArguments("permanent request rejection")

    rig[2].publish = denied
    rig[4].recover()
    rig[4].recover()
    current = rig[1].get(task["id"])
    assert current["status"] == "needs_attention" and len(calls) == 1
    assert current["data"]["report"]["passed"]
    assert current["data"]["last_error"]["kind"] == "invalid_arguments"


def test_native_verified_publication_rejection_keeps_artifacts_for_attention(rig, tmp_path):
    desktop = Desktop(rig[4])
    action = desktop.tick()

    def denied(*args, **kwargs):
        raise AuthenticationBlocked("permission denied")

    rig[2].publish = denied
    complete(desktop, action, tmp_path)
    current = rig[1].get(action["task_id"])
    assert current["status"] == "needs_attention"
    assert current["data"]["report"]["passed"] and rig[1].meta("paused")
    assert not rig[2].published


def test_authentication_failure_pauses_native_controller_without_fallback(rig):
    def denied(*args):
        raise AuthenticationBlocked("blocked")

    rig[2].discover = denied
    assert Desktop(rig[4]).tick()["action"] == "wait"
    assert rig[1].meta("paused") and not rig[1].desktop_jobs()
    assert not rig[3].calls and not rig[2].published


def test_validation_retains_error_category_but_never_retries_the_tests(rig):
    from conftest import BASE, FIX, REGRESSION

    calls = []

    class FailingCheck(FixtureSandbox):
        def command(self, argv):
            calls.append(argv)
            raise CommandTimeout("test may have side effects")

    engine = Engine(rig[0], rig[1], rig[2], rig[3], FailingCheck)
    report = engine.validate(rig[0].repositories[0], BASE,
                             {"calc.py": FIX, "tests/test_oss_regression_1.py": REGRESSION},
                             "tests/test_oss_regression_1.py", "a" * 40, time.monotonic() + 30)
    assert not report.passed and report.error.kind == ErrorKind.TIMEOUT
    assert len(calls) == 1
    with pytest.raises(ValidationFailure):
        run_retry(lambda _: (_ for _ in ()).throw(ValidationFailure("verification", cause_info=report.error)))


def test_standalone_cancelled_retry_does_not_overwrite_a_stopped_task(rig, monkeypatch):
    codex, _ = scripted_codex(rig[0], monkeypatch, [call(1)])
    engine = Engine(rig[0], rig[1], rig[2], codex, FixtureSandbox)
    ident = rig[1].create_task(rig[2].issue_value)
    original = FixtureSandbox.tool

    def stop(self, name, arguments, writable=False):
        rig[1].update(ident, "stopped")
        raise TransientFailure()

    monkeypatch.setattr(FixtureSandbox, "tool", stop)
    rig[0].limits.retries.base_delay_seconds = 0
    engine.process(rig[1].get(ident))
    assert rig[1].get(ident)["status"] == "stopped"
    assert not rig[2].published
    monkeypatch.setattr(FixtureSandbox, "tool", original)


def test_invalid_native_tool_parameters_can_be_corrected_without_auto_retry(rig):
    desktop, action = native(rig)
    with toolbox(rig, action) as worker:
        with pytest.raises(InvalidArguments):
            worker.tool("repo_read", {"wrong_key": "calc.py"})
        assert "def add" in worker.tool("repo_read", {"path": "calc.py"})
    state = rig[1].desktop_job(action["ticket"])["data"]["tool_activity"]
    assert state["steps"] == 3 and state["retries_used"] == 0
    assert state["last_error"]["kind"] == "invalid_arguments"
    assert desktop.tick()["action"] == "resume"


def test_rpc_tool_error_has_structured_classification():
    from test_protocol_cli_store import rpc

    def invalid(*args):
        raise InvalidArguments("correct the path key")

    client, process = rpc([call(1)], invalid)
    client.receive()
    response = json.loads(process.stdin.getvalue())["result"]
    error = json.loads(response["contentItems"][0]["text"])["error"]
    assert not response["success"]
    assert error["kind"] == "invalid_arguments" and error["action"] == "correct_input"
