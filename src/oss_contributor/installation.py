"""Upgrade vendor-managed skill resources without overwriting operator customization."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

from . import __version__
from .process import ContributorError

# Recognize the first release, which predates installation manifests.
LEGACY = {
    "SKILL.md": "f2b6e41deb228652ab08b05649fc82b7d464eac61878e8f0c79d186132d06e77",
    "agents/openai.yaml": "21a131fee5e28b62d5a2cf38e29b0b18257625f64539756724f8e0ad2752da2a",
}
SOURCE = Path(__file__).parent / "skills" / "oss-contributor"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_path(root: Path, relative: str):
    for part in [Path(relative), *Path(relative).parents]:
        if (root / part).is_symlink():
            raise ContributorError(f"skill upgrade refuses linked paths: {relative}")


def atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=".oss-install-", delete=False
    ) as stream:
        temporary = Path(stream.name)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        temporary.chmod(0o644)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def install_skill(destination: Path, config: Path | None = None, upgrade: bool = False) -> Path:
    source = SOURCE
    root = destination.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = root / "oss-contributor"
    with (root / ".oss-contributor-install.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if target.is_symlink():
            raise ContributorError("skill installation refuses a linked destination")
        if target.exists() and not upgrade:
            raise ContributorError(
                f"skill already exists: {target}; use --upgrade for managed resources"
            )
        resources = {
            str(path.relative_to(source)): path.read_bytes()
            for path in source.rglob("*")
            if path.is_file()
        }
        if "SKILL.md" not in resources:
            raise ContributorError("installed package is missing the bundled skill")
        hashes = {name: digest(data) for name, data in resources.items()}
        for name in [*resources, "installation.json", ".install-manifest.json"]:
            check_path(target, name)
        manifest_path = target / ".install-manifest.json"
        manifest = (
            json.loads(manifest_path.read_text())
            if manifest_path.exists()
            else {"format_version": 1, "files": LEGACY}
        )
        previous = manifest.get("files")
        if manifest.get("format_version") != 1 or not isinstance(previous, dict):
            raise ContributorError("unsupported skill installation manifest")
        conflicts = []
        for name in resources:
            installed = target / name
            if installed.exists() and digest(installed.read_bytes()) not in {
                previous.get(name),
                hashes[name],
            }:
                conflicts.append(name)
        if conflicts:
            raise ContributorError(
                "locally modified skill resources preserved: " + ", ".join(conflicts)
            )
        metadata_path = target / "installation.json"
        metadata = json.loads(metadata_path.read_text()) if metadata_path.exists() else {}
        chosen_config = (
            config.expanduser().resolve()
            if config is not None
            else Path(
                metadata.get("config", str(Path.home() / ".config/oss-contributor/config.toml"))
            )
            .expanduser()
            .resolve()
        )
        # Accept either the old managed hash or this release's hash on retry after interruption.
        for name, data in resources.items():
            atomic_write(target / name, data)
        atomic_write(
            metadata_path,
            json.dumps({"python": sys.executable, "config": str(chosen_config)}, indent=2).encode(),
        )
        atomic_write(
            manifest_path,
            json.dumps(
                {"format_version": 1, "version": __version__, "files": hashes}, indent=2
            ).encode(),
        )
        return target
