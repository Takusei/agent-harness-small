"""Trusted file tool, sent over stdin and executed only inside Docker.

Hash-before-edit and bounded file reads follow the sibling coding-agent prototype;
this implementation is independent of its orchestration runtime.
"""

import hashlib
import json
import os
import stat
import sys
from pathlib import Path

MAX_FILE = 1_000_000
IGNORED = {".git", ".venv", "__pycache__", ".pytest_cache", "node_modules"}


def safe_path(root: Path, name: str) -> Path:
    relative = Path(name)
    if not name or relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Expected relative path inside workspace")
    path = root
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("Symlinks are not supported")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Path escapes workspace")
    return path


def read_bytes(path: Path) -> bytes:
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as f:
        info = os.fstat(f.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE:
            raise ValueError("Expected regular file <= 1 MB")
        data = f.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        raise ValueError("File too large")
    return data


def files(root):
    visited = 0
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(
            d for d in dirs if d not in IGNORED and not (Path(directory) / d).is_symlink()
        )
        for name in sorted(names):
            visited += 1
            if visited > 2000:
                raise ValueError("Workspace scan limit exceeded")
            path = Path(directory) / name
            if not path.is_symlink():
                yield path


def dispatch(root: Path, request: dict) -> dict:
    op = request.pop("op")
    if op in {"list_files", "search_files"}:
        matches = []
        for path in files(root):
            name = str(path.relative_to(root))
            if op == "list_files":
                if request["query"] in name:
                    matches.append(name)
            else:
                try:
                    lines = read_bytes(path).decode("utf-8").splitlines()
                except (ValueError, OSError, UnicodeError):
                    continue
                for line_number, line in enumerate(lines, 1):
                    if request["query"] in line:
                        matches.append({"path": name, "line": line_number, "text": line[:200]})
                    if len(matches) >= 40:
                        break
            if len(matches) >= 40:
                return {"matches": matches, "truncated": True}
        return {"matches": matches, "truncated": False}
    path = safe_path(root, request["path"])
    data = read_bytes(path)
    digest = hashlib.sha256(data).hexdigest()
    content = data.decode("utf-8")
    if op == "read_file":
        start, count = request["start"], request["count"]
        lines = content.splitlines(keepends=True)
        text = "".join(lines[start - 1 : start - 1 + count])
        return {
            "sha256": digest,
            "content": text[:8000],
            "total_lines": len(lines),
            "truncated": len(text) > 8000 or start - 1 + count < len(lines),
        }
    if op != "edit_file":
        raise ValueError("Unknown file operation")
    if request.get("expected_hash") != digest:
        raise ValueError("Missing or stale read; read the file again")
    old, new = request["old"], request["new"]
    if not old or content.count(old) != 1:
        raise ValueError("old must match exactly once")
    replacement = content.replace(old, new, 1).encode()
    if len(replacement) > MAX_FILE:
        raise ValueError("Replacement exceeds 1 MB")
    # Each operation runs alone in a fresh container. Command process trees are
    # destroyed before any subsequent file tool is started.
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_NOFOLLOW), "wb") as f:
        f.write(replacement)
        f.truncate()
    return {"edited": request["path"]}


if __name__ == "__main__":
    try:
        print(json.dumps(dispatch(Path("/workspace"), json.loads(sys.argv[1]))))
    except (ValueError, OSError, KeyError, UnicodeError) as exc:
        print(json.dumps({"error": str(exc)}))
