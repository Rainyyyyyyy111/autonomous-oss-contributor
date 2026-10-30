"""Trusted container-side file bridge; no host mounts, no credentials."""

import base64
import json
import os
import stat
import sys

ROOT = "/work"
LIMIT = 4 * 1024 * 1024
IGNORE = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "node_modules",
    ".tox",
    ".nox",
    "build",
    "dist",
    ".coverage",
}


def fd_for(path, create=False):
    if (
        not isinstance(path, str)
        or not path
        or path.startswith("/")
        or "\\" in path
        or any(p in {"", ".", ".."} or p in IGNORE for p in path.split("/"))
        or any(ord(c) < 32 for c in path)
    ):
        raise ValueError("unsafe path")
    parts = path.split("/")
    fd = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            if create:
                try:
                    os.mkdir(part, dir_fd=fd)
                except FileExistsError:
                    pass
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        return fd, parts[-1]
    except BaseException:
        os.close(fd)
        raise


def read(path, binary=False):
    directory, name = fd_for(path)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("not a regular file")
            raw = stream.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise ValueError("file too large")
        try:
            text = raw.decode()
            if "\x00" in text:
                raise UnicodeError()
            return text
        except UnicodeError:
            if binary:
                return "\x00base64:" + base64.b64encode(raw).decode()
            raise ValueError("binary file") from None
    finally:
        os.close(directory)


def write(path, content):
    directory, name = fd_for(path, create=True)
    try:
        if content is None:
            os.unlink(name, dir_fd=directory)
        else:
            if not isinstance(content, str) or "\x00" in content or len(content.encode()) > LIMIT:
                raise ValueError("invalid content")
            fd = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o644,
                dir_fd=directory,
            )
            with os.fdopen(fd, "w") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError("not a regular file")
                stream.write(content)
    finally:
        os.close(directory)


def paths():
    count = 0
    for directory, dirs, files in os.walk(ROOT, followlinks=False):
        for name in dirs + files:
            if os.path.islink(os.path.join(directory, name)):
                raise ValueError("symlink in workspace")
        dirs[:] = [d for d in dirs if d not in IGNORE and not d.endswith(".egg-info")]
        for name in files:
            if name in IGNORE:
                continue
            count += 1
            if count > 30_000:
                raise ValueError("too many files")
            yield os.path.relpath(os.path.join(directory, name), ROOT)


def main():
    request = json.load(sys.stdin)
    action = request["action"]
    if action == "read":
        return read(request["path"])
    if action == "write":
        for path, content in request["changes"].items():
            write(path, content)
        return "changes applied"
    if action == "snapshot":
        snapshot = {}
        size = 0
        for path in paths():
            content = read(path, binary=True)
            size += len(content)
            if size > 60_000_000:
                raise ValueError("workspace too large")
            snapshot[path] = content
        return snapshot
    if action == "search":
        pattern = request["pattern"]
        if not isinstance(pattern, str) or not pattern or len(pattern) > 200:
            raise ValueError("invalid literal search")
        result = []
        for path in paths():
            try:
                for n, line in enumerate(read(path).splitlines(), 1):
                    if pattern.casefold() in line.casefold():
                        result.append({"path": path, "line": n, "text": line[:500]})
                        if len(result) >= 100:
                            return result
            except ValueError as exc:
                if str(exc) != "binary file":
                    raise
        return result
    if action == "list":
        return list(paths())
    raise ValueError("unknown action")


if __name__ == "__main__":
    try:
        print(json.dumps({"result": main()}))
    except Exception as error:
        print(json.dumps({"error": str(error)}))
        sys.exit(1)
