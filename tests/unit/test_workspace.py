import os
import subprocess

import pytest

from agent_harness.workspace import copy_snapshot, make_patch, snapshot


def test_snapshot_rejects_symlinks_and_skips_secrets(tmp_path):
    (tmp_path / "ok.py").write_text("hello\n")
    (tmp_path / ".env").write_text("DO_NOT_COPY=secret")
    assert set(snapshot(tmp_path)) == {"ok.py"}
    (tmp_path / "escape").symlink_to("/etc/passwd")
    with pytest.raises(ValueError, match="Symlink"):
        snapshot(tmp_path)


def test_special_files_rejected_without_blocking(tmp_path):
    os.mkfifo(tmp_path / "fifo")
    with pytest.raises(ValueError, match="regular"):
        snapshot(tmp_path)


def test_patch_applies_added_deleted_mode_and_missing_newline(tmp_path):
    before = {"a file.py": (b"before", 0o644), "deleted.py": (b"old\n", 0o644)}
    after = {"a file.py": (b"after", 0o755), "added.py": (b"new\n", 0o644)}
    destination = tmp_path / "copy"
    copy_snapshot(before, destination)
    patch = make_patch(before, after)
    # git applies static text; no repository or generated code is executed here.
    subprocess.run(
        ["git", "apply", "--unsafe-paths", "-"],
        input=patch.encode(),
        cwd=destination,
        check=True,
        capture_output=True,
    )
    assert snapshot(destination) == after
