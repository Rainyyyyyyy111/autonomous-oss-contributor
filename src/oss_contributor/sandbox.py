from __future__ import annotations

import base64
import io
import json
import os
import re
import tarfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .config import Repository
from .errors import Cancelled, EnvironmentFailure, InvalidArguments, PermissionDenied
from .models import CommandResult
from .process import ContributorError, run_command
from .safety import BINARY_PREFIX, MAX_ARCHIVE, PROTECTED, is_test, safe_path

RUNTIME = Path(__file__).parent / "runtime"


class DockerSandbox:
    def __init__(
        self, repo: Repository, files: dict[str, str], deadline: float, owner: str | None = None
    ):
        self.repo, self.files, self.deadline = repo, files, deadline
        self.name = "oss-worker-" + uuid.uuid4().hex
        self.network = self.name + "-internal"
        self.proxy = self.name + "-proxy"
        self.volume = self.name + "-work"
        self.closed = False
        self.owner = owner or f"manual-{os.getpid()}"

    @staticmethod
    def cleanup_owner(owner: str):
        """Called under the state directory's exclusive worker lock on startup."""
        selector = f"label=oss-contributor-owner={owner}"
        for listing, removal in (
            (["ps", "-aq"], ["rm", "-f"]),
            (["network", "ls", "-q"], ["network", "rm"]),
            (["volume", "ls", "-q"], ["volume", "rm", "-f"]),
        ):
            result = run_command(["docker", *listing, "--filter", selector], timeout=15, check=True)
            ids = result.output.split()
            if ids:
                run_command(["docker", *removal, *ids], timeout=30, check=True)

    def remaining(self) -> float:
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise Cancelled("task deadline exceeded")
        return left

    def docker(
        self, *args: str, input_text: str | None = None, output: int = 100_000, check: bool = True
    ) -> CommandResult:
        parameters = list(args)
        if parameters[:2] in (["volume", "create"], ["network", "create"]) or parameters[:1] == [
            "run"
        ]:
            position = 1 if parameters[0] == "run" else 2
            parameters[position:position] = ["--label", f"oss-contributor-owner={self.owner}"]
        return run_command(
            ["docker", *parameters],
            timeout=min(120, self.remaining()),
            input_text=input_text,
            check=check,
            max_output=output,
        )

    @staticmethod
    def doctor() -> dict:
        result = run_command(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=10)
        return {"ok": result.code == 0, "detail": result.output.strip()}

    @staticmethod
    def build():
        return run_command(
            ["docker", "build", "-t", "oss-contributor-runner:0.1", str(RUNTIME)],
            timeout=600,
            check=True,
        )

    def __enter__(self):
        try:
            self.docker("volume", "create", "--label", "oss-contributor=true", self.volume)
            self.docker(
                "volume", "create", "--label", "oss-contributor=true", self.volume + "-venv"
            )
            self.docker(
                "network", "create", "--internal", "--label", "oss-contributor=true", self.network
            )
            self.docker(
                "run",
                "-d",
                "--name",
                self.name,
                "--label",
                "oss-contributor=true",
                "--user",
                "10001:10001",
                "--network",
                self.network,
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--pids-limit=128",
                "--cpus=2",
                "--memory=4g",
                "--memory-swap=4g",
                "--mount",
                f"type=volume,src={self.volume},dst=/work",
                "--mount",
                f"type=volume,src={self.volume}-venv,dst=/opt/venv",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,size=256m,mode=1777",
                "--workdir",
                "/work",
                self.repo.image,
                "sleep",
                str(max(1, int(self.remaining()))),
            )
            self.docker("network", "disconnect", self.network, self.name)
            # Copy only sanitized bytes through docker stdin; no bind mounts.
            payload = io.BytesIO()
            with tarfile.open(fileobj=payload, mode="w") as archive:
                directories: set[str] = set()
                for path in self.files:
                    safe_path(path)
                    directories.update(
                        p.as_posix() for p in Path(path).parents if p.as_posix() != "."
                    )
                for path in sorted(directories, key=lambda p: (p.count("/"), p)):
                    info = tarfile.TarInfo(path + "/")
                    info.type = tarfile.DIRTYPE
                    info.uid = info.gid = 10001
                    info.mode = 0o755
                    archive.addfile(info)
                for path, content in self.files.items():
                    safe_path(path)
                    raw = (
                        base64.b64decode(content[len(BINARY_PREFIX) :])
                        if content.startswith(BINARY_PREFIX)
                        else content.encode()
                    )
                    info = tarfile.TarInfo(path)
                    info.uid = info.gid = 10001
                    info.mode = 0o644
                    info.size = len(raw)
                    archive.addfile(info, io.BytesIO(raw))
            import subprocess

            proc = subprocess.run(
                ["docker", "cp", "-a", "-", f"{self.name}:/work"],
                input=payload.getvalue(),
                capture_output=True,
                timeout=min(60, self.remaining()),
            )
            if proc.returncode:
                raise ContributorError("failed to populate container workspace")
            self.assert_isolated()
            return self
        except BaseException:
            self.close()
            raise

    def assert_isolated(self):
        data = json.loads(self.docker("inspect", self.name).output)[0]
        host = data["HostConfig"]
        if (
            host["Privileged"]
            or not host["ReadonlyRootfs"]
            or data["Config"]["User"] != "10001:10001"
            or data["NetworkSettings"]["Networks"]
            or any(
                m["Type"] != "volume" or m["Destination"] not in {"/work", "/opt/venv"}
                for m in data["Mounts"]
                if m["Type"] != "tmpfs"
            )
            or host.get("Binds")
            or host.get("Devices")
            or "ALL" not in (host.get("CapDrop") or [])
        ):
            raise ContributorError("container isolation check failed")
        env = data["Config"].get("Env", [])
        if any(re.search(r"(?:TOKEN|API_KEY|SECRET|PASSWORD)=", item, re.I) for item in env):
            raise ContributorError("runner image contains credentials")
        if self.command(["id", "-u"]).output.strip() != "10001":
            raise ContributorError("worker is not non-root")

    @contextmanager
    def install_network(self):
        try:
            self.docker(
                "run",
                "-d",
                "--name",
                self.proxy,
                "--label",
                "oss-contributor=true",
                "--network",
                self.network,
                "--network-alias",
                "pypi-proxy",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--pids-limit=32",
                "--memory=128m",
                "oss-contributor-runner:0.1",
                "/usr/local/bin/python",
                "-I",
                "/usr/local/lib/oss_proxy.py",
            )
            self.docker("network", "connect", "bridge", self.proxy)
            self.docker("network", "connect", self.network, self.name)
            yield
        finally:
            self._cleanup("network", "disconnect", self.network, self.name)
            self._cleanup("rm", "-f", self.proxy)
            self.assert_isolated()

    def install(self) -> list[CommandResult]:
        if not self.repo.install:
            return []
        results = []
        with self.install_network():
            for argv in self.repo.install:
                result = self.command(argv, installation=True)
                results.append(result)
                if result.code:
                    raise EnvironmentFailure(
                        f"dependency installation failed: {result.output[-3000:]}"
                    )
        return results

    def command(self, argv: list[str], installation: bool = False) -> CommandResult:
        if (
            not argv
            or len(argv) > 100
            or not re.fullmatch(r"[A-Za-z0-9_/.-]+", argv[0])
            or any("\x00" in arg or len(arg) > 10_000 for arg in argv)
        ):
            raise InvalidArguments("invalid container command")
        args = ["exec", "--workdir", "/work"]
        if installation:
            args += [
                "--env",
                "HTTPS_PROXY=http://pypi-proxy:8888",
                "--env",
                "HTTP_PROXY=http://pypi-proxy:8888",
                "--env",
                "PIP_INDEX_URL=https://pypi.org/simple",
                "--env",
                "NO_PROXY=",
            ]
        result = self.docker(*args, self.name, *argv, check=False)
        return result.model_copy(update={"argv": argv})

    def bridge(self, action: str, **params) -> Any:
        result = self.docker(
            "exec",
            "-i",
            self.name,
            "/usr/local/bin/python",
            "-I",
            "/usr/local/lib/oss_bridge.py",
            input_text=json.dumps({"action": action, **params}),
            check=False,
            output=MAX_ARCHIVE * 2,
        )
        try:
            data = json.loads(result.output)
        except ValueError:
            raise ContributorError("invalid sandbox bridge response") from None
        if result.code or "error" in data:
            raise ContributorError(f"sandbox bridge rejected request: {data.get('error', '')}")
        return data["result"]

    def snapshot(self) -> dict[str, str]:
        return self.bridge("snapshot")

    def apply(self, changes: dict[str, str | None]):
        for path in changes:
            safe_path(path)
        return self.bridge("write", changes=changes)

    def tool(self, name: str, arguments: dict, *, writable: bool = False) -> Any:
        if name == "repo_apply" and not writable:
            raise PermissionDenied("source edits are disabled in this stage")
        if name == "repo_list" and arguments == {}:
            return self.bridge("list")
        if name == "repo_read" and set(arguments) == {"path"}:
            safe_path(arguments["path"])
            return self.bridge("read", **arguments)
        if name == "repo_search" and set(arguments) == {"pattern"}:
            return self.bridge("search", **arguments)
        if name == "sandbox_exec" and set(arguments) == {"argv"}:
            if not isinstance(arguments["argv"], list) or not all(
                isinstance(x, str) for x in arguments["argv"]
            ):
                raise InvalidArguments("argv must be an array of strings")
            return self.command(arguments["argv"]).model_dump()
        if name == "repo_apply" and writable and set(arguments) == {"changes"}:
            changes = arguments["changes"]
            if not isinstance(changes, dict) or len(changes) > 5:
                raise InvalidArguments("invalid changes")
            for path, content in changes.items():
                safe_path(path)
                if PROTECTED.search(path) or is_test(path) or not path.endswith(".py"):
                    raise PermissionDenied(
                        "agent may only edit Python source, not tests or configuration"
                    )
                if content is not None and not isinstance(content, str):
                    raise InvalidArguments("invalid content")
            return self.apply(changes)
        if name in {"repo_list", "repo_read", "repo_search", "sandbox_exec", "repo_apply"}:
            raise InvalidArguments("invalid arguments for repository tool")
        raise PermissionDenied(f"tool is not authorized: {name}")

    def _cleanup(self, *args):
        run_command(["docker", *args], timeout=10)

    def close(self):
        if self.closed:
            return
        self.closed = True
        for args in (
            ("rm", "-f", self.name, self.proxy),
            ("network", "rm", self.network),
            ("volume", "rm", "-f", self.volume, self.volume + "-venv"),
        ):
            try:
                self._cleanup(*args)
            except (ContributorError, OSError):
                pass

    def __exit__(self, *_args):
        self.close()
