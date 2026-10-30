from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from .models import CommandResult


class ContributorError(RuntimeError):
    pass


class RateLimited(ContributorError):
    def __init__(self, seconds: int = 600):
        self.seconds = seconds
        super().__init__(f"GitHub rate limited; retry after {seconds} seconds")


class QuotaExhausted(ContributorError):
    pass


class PublicationConflict(ContributorError):
    """A permanent conflict requires attention instead of publication retries."""


def run_bytes(
    argv: list[str],
    *,
    timeout: float = 60,
    cwd: Path | None = None,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
    max_output: int = 100_000,
    reject_truncation: bool = False,
    merge_stderr: bool = True,
) -> tuple[int, bytes, float]:
    """No shell; bounded reads and whole-process-group cancellation."""
    started = time.monotonic()
    with tempfile.TemporaryFile() as source:
        if input_text is not None:
            source.write(input_text.encode())
            source.seek(0)
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=source if input_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT if merge_stderr else subprocess.DEVNULL,
            start_new_session=True,
        )
        captured = bytearray()
        truncated = False

        def drain():
            nonlocal truncated
            assert proc.stdout is not None
            while chunk := proc.stdout.read(8192):
                if not truncated and len(captured) + len(chunk) > max_output:
                    truncated = True
                    if reject_truncation and proc.poll() is None:
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except OSError:
                            # The process can exit between poll and killpg on macOS.
                            pass
                if len(captured) < max_output:
                    captured.extend(chunk[: max_output - len(captured)])

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            raise ContributorError(f"Command timed out or interrupted: {argv[0]}") from None
        except KeyboardInterrupt:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            raise
        finally:
            reader.join(timeout=2)
            if proc.stdout:
                proc.stdout.close()
        if truncated and reject_truncation:
            raise ContributorError(f"command output exceeds size limit: {argv[0]}")
        return proc.returncode, bytes(captured), time.monotonic() - started


def run_command(
    argv: list[str],
    *,
    timeout: float = 60,
    cwd: Path | None = None,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
    check: bool = False,
    max_output: int = 100_000,
) -> CommandResult:
    code, output, seconds = run_bytes(
        argv, timeout=timeout, cwd=cwd, input_text=input_text, env=env, max_output=max_output
    )
    result = CommandResult(
        argv=argv, code=code, output=output.decode("utf-8", "replace"), seconds=seconds
    )
    if check and result.code:
        raise ContributorError(f"{argv[0]} failed ({result.code}): {result.output[-4000:]}")
    return result
