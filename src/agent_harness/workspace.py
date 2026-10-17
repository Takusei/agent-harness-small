"""Bounded, text-only snapshots and reviewable patches; never execute repo code."""

import difflib
import json
import os
import stat
from pathlib import Path

from agent_harness.worker import read_bytes

IGNORED = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules"}
SENSITIVE = {".env", ".aws", ".ssh", ".codex"}


def snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    result = {}
    total = 0
    visited = 0
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED | SENSITIVE)
        for name in [*dirs, *sorted(names)]:
            if name in IGNORED | SENSITIVE or name.startswith(".env."):
                continue
            visited += 1
            if visited > 2000:
                raise ValueError("Snapshot exceeds 2000 entries")
            path = Path(directory) / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise ValueError(f"Symlink not supported: {path.relative_to(root)}")
            if stat.S_ISDIR(info.st_mode):
                continue
            data = read_bytes(path)
            data.decode("utf-8")  # Milestone 1 deliberately supports small text repos.
            total += len(data)
            if total > 20_000_000:
                raise ValueError("Snapshot exceeds 20 MB")
            relative = str(path.relative_to(root))
            result[relative] = (data, 0o755 if info.st_mode & 0o111 else 0o644)
    return result


def copy_snapshot(files: dict, destination: Path):
    destination.mkdir(parents=True, exist_ok=False)
    for name, (data, mode) in files.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(mode)


def make_patch(before: dict, after: dict) -> str:
    parts = []
    for name in sorted(before.keys() | after.keys()):
        old = before.get(name)
        new = after.get(name)
        if old == new:
            continue
        a, b = json.dumps("a/" + name), json.dumps("b/" + name)
        parts.append(f"diff --git {a} {b}\n")
        if old is None:
            parts.append(f"new file mode 100{new[1]:o}\n")
        elif new is None:
            parts.append(f"deleted file mode 100{old[1]:o}\n")
        elif old[1] != new[1]:
            parts.extend([f"old mode 100{old[1]:o}\n", f"new mode 100{new[1]:o}\n"])
        old_text = old[0].decode() if old else ""
        new_text = new[0].decode() if new else ""
        lines = difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=a if old else "/dev/null",
            tofile=b if new else "/dev/null",
        )
        for line in lines:
            parts.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return "".join(parts)
