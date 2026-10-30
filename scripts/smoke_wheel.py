"""Install the built wheel outside the checkout and verify CLI/runtime packaging."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    wheels = list((root / "dist").glob(f"autonomous_oss_contributor-{version}-*.whl"))
    if len(wheels) != 1:
        raise SystemExit("Build exactly one current Alpha wheel with uv build first")
    wheel = wheels[0]
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        for resource in ("Dockerfile", "bridge.py", "proxy.py"):
            assert f"oss_contributor/runtime/{resource}" in names, resource
        assert "oss_contributor/skills/oss-contributor/SKILL.md" in names
        assert any(name.endswith("/licenses/LICENSE") for name in names)
        assert not any("auth.json" in name or ".sqlite" in name for name in names)

    with tempfile.TemporaryDirectory(prefix="oss-wheel-smoke-") as temporary:
        work = Path(temporary)
        env = work / "venv"
        subprocess.run(
            ["uv", "venv", "--python", sys.executable, str(env)], cwd=work, check=True
        )
        python = env / "bin/python"
        cli = env / "bin/oss-contributor"
        subprocess.run(
            ["uv", "pip", "install", "--python", str(python), str(wheel)],
            cwd=work,
            check=True,
        )
        subprocess.run(
            [str(python), "-I", "-c",
             "from oss_contributor.sandbox import RUNTIME; "
             "assert all((RUNTIME / f).is_file() for f in ('Dockerfile','bridge.py','proxy.py'))"],
            cwd=work,
            check=True,
        )
        subprocess.run([str(cli), "--help"], cwd=work, check=True, stdout=subprocess.DEVNULL)
        config = work / "config.toml"
        subprocess.run(
            [str(cli), "init", "--config", str(config)],
            cwd=work,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        # Redirect every state write away from the user's actual state directory.
        config.write_text(
            f"state_dir = {json.dumps(str(work / 'state'))}\n" + config.read_text()
        )
        result = subprocess.run(
            [str(cli), "status", "--config", str(config)],
            cwd=work,
            text=True,
            capture_output=True,
            check=True,
        )
        assert json.loads(result.stdout)["tasks"] == []
        subprocess.run(
            [str(cli), "desktop", "install-skill", "--config", str(config), "--directory", str(work / "skills")],
            cwd=work, check=True, stdout=subprocess.DEVNULL,
        )
        assert (work / "skills/oss-contributor/SKILL.md").is_file()
    print("Fresh wheel install, CLI, empty state, license and runtime resources: passed")


if __name__ == "__main__":
    main()
