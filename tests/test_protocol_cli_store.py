import io
import json
import sys
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from oss_contributor.cli import app
from oss_contributor.codex import RPC, strict_schema, tools
from oss_contributor.config import load_config
from oss_contributor.models import Assessment
from oss_contributor.process import ContributorError, run_bytes, run_command
from oss_contributor.store import Store


def rpc(messages, handler=None):
    import time

    process = SimpleNamespace(
        stdin=io.BytesIO(),
        stdout=io.BytesIO(b"".join(json.dumps(m).encode() + b"\n" for m in messages)),
    )
    return RPC(process, time.monotonic() + 2, handler), process


def test_dynamic_tool_is_dispatched_through_controller_only():
    calls = []
    client, process = rpc(
        [
            {
                "id": "server-1",
                "method": "item/tool/call",
                "params": {"tool": "repo_read", "arguments": {"path": "calc.py"}},
            }
        ],
        lambda name, args: calls.append((name, args)) or "source",
    )
    client.receive()
    assert calls == [("repo_read", {"path": "calc.py"})]
    assert json.loads(process.stdin.getvalue())["result"]["success"]


@pytest.mark.parametrize(
    "method",
    [
        "item/commandExecution/requestApproval",
        "account/chatgptAuthTokens/refresh",
        "exec",
        "shell",
        "item/fileChange/requestApproval",
    ],
)
def test_host_execution_and_unknown_requests_abort(method):
    client, process = rpc([{"id": "unsafe", "method": method, "params": {}}])
    with pytest.raises(ContributorError, match="unauthorized"):
        client.receive()
    assert "error" in json.loads(process.stdin.getvalue())


@pytest.mark.parametrize(
    "kind", ["commandExecution", "fileChange", "webSearch", "mcpToolCall", "collabToolCall"]
)
def test_unexpected_native_capabilities_abort(kind):
    client, _ = rpc([{"method": "item/started", "params": {"item": {"type": kind}}}])
    with pytest.raises(ContributorError, match="native"):
        client.receive()


def test_tool_sets_do_not_offer_host_or_publication_capabilities():
    read_names = {x["name"] for x in tools(False)}
    write_names = {x["name"] for x in tools(True)}
    assert read_names == {"repo_list", "repo_read", "repo_search"}
    assert write_names == read_names | {"sandbox_exec", "repo_apply"}


def test_schema_requires_all_fields_and_rejects_extra_keys():
    schema = strict_schema(Assessment)
    assert set(schema["required"]) == set(schema["properties"])
    assert not schema["additionalProperties"]


def test_cli_init_status_pause_resume_inspect(tmp_path):
    runner = CliRunner()
    path = tmp_path / "config.toml"
    assert runner.invoke(app, ["init", "-c", str(path)]).exit_code == 0
    assert not load_config(path).repositories
    assert runner.invoke(app, ["init", "-c", str(path)]).exit_code != 0
    path.write_text(f"state_dir = {json.dumps(str(tmp_path / 'state'))}\n" + path.read_text())
    assert runner.invoke(app, ["pause", "-c", str(path)]).exit_code == 0
    status = runner.invoke(app, ["status", "-c", str(path)])
    assert json.loads(status.stdout)["paused"]
    assert runner.invoke(app, ["run", "--once", "-c", str(path)]).exit_code == 0
    assert runner.invoke(app, ["resume", "-c", str(path)]).exit_code == 0
    assert not json.loads(runner.invoke(app, ["status", "-c", str(path)]).stdout)["paused"]
    assert runner.invoke(app, ["run", "-c", str(path)]).exit_code != 0


def test_store_unique_issue_and_exclusive_worker_lock(rig):
    store, gh = rig[1], rig[2]
    assert store.create_task(gh.issue_value)
    assert store.create_task(gh.issue_value) is None
    second = Store(rig[0])
    try:
        with store.lock(), pytest.raises(ContributorError, match="another worker"), second.lock():
            pass
    finally:
        second.close()


def test_process_output_is_bounded_and_shell_text_is_literal():
    text = "$(touch /tmp/oss-should-never-exist); `echo bad`"
    result = run_command(
        [sys.executable, "-c", "import sys; print(sys.argv[1]); print('x'*100000)", text],
        max_output=200,
    )
    assert result.code == 0
    assert len(result.output) == 200
    assert result.output.startswith(text)


def test_process_timeout_cancels_group():
    with pytest.raises(ContributorError, match="timed out"):
        run_command([sys.executable, "-c", "import time; time.sleep(20)"], timeout=0.05)


def test_oversized_archive_stream_is_rejected_without_disk_spooling():
    with pytest.raises(ContributorError, match="size limit"):
        run_bytes(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x'*1000000)"],
            max_output=1000,
            reject_truncation=True,
        )
