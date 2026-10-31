"""Prepare trusted runner images without retaining target workspaces or credentials."""

from __future__ import annotations

import hashlib
import json
import re
import time

from .config import Repository
from .process import ContributorError, run_command
from .sandbox import DockerSandbox
from .store import Store


class Environments:
    def __init__(self, store: Store):
        self.store = store

    @staticmethod
    def key(image: str) -> str:
        return "environment:" + hashlib.sha256(image.encode()).hexdigest()

    def reports(self) -> list[dict]:
        return [json.loads(row[0]) for row in self.store.db.execute(
            "SELECT value FROM meta WHERE key LIKE 'environment:%' ORDER BY key",
        )]

    def prepare(self, repo: Repository, *, refresh: bool = False) -> dict:
        key = self.key(repo.image)
        report = {"image": repo.image, "ok": False, "checked_at": time.time()}
        try:
            inspected = run_command(
                ["docker", "image", "inspect", "--format", "{{.Id}}", repo.image], timeout=15,
            )
            if inspected.code:
                raise ContributorError("cannot inspect configured local image: " + inspected.output[-1000:])
            # Docker may prepend client warnings to merged stdout/stderr. Accept only a
            # complete immutable ID line, never an arbitrary prefix or shell fragment.
            ids = re.findall(r"(?m)^sha256:[a-f0-9]{64}$", inspected.output)
            if len(ids) != 1:
                raise ContributorError("invalid Docker image inspection response: " + inspected.output[-1000:])
            image_id = ids[0]
            previous = json.loads(self.store.meta(key, "{}"))
            if (not refresh and previous.get("ok") and previous.get("image_id") == image_id
                    and time.time() - previous["checked_at"] < 3600):
                return previous
            # Pin this probe to the inspected immutable image; no target code/install/snapshot reuse.
            probe = repo.model_copy(update={"image": image_id, "install": [], "tests": []})
            with DockerSandbox(probe, {}, time.monotonic() + 60) as sandbox:
                sandbox.assert_isolated()
            report.update(ok=True, image_id=image_id, detail="fresh non-root offline isolation probe passed")
        except (ContributorError, OSError, ValueError) as exc:
            report["detail"] = str(exc)[:1000]
        self.store.set_meta(key, json.dumps(report))
        return report
