"""Real Docker tests. Never substitute host execution when Docker is unavailable."""

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from agent_harness.execution import DockerExecutor
from agent_harness.providers import ScriptedProvider, demo_actions
from agent_harness.runtime import run
from agent_harness.workspace import snapshot

pytestmark = pytest.mark.docker
IMAGE = "agent-harness-exec:local"
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session", autouse=True)
def image(request):
    if not request.config.getoption("--run-docker"):
        return
    # Explicit Docker tests fail if unavailable: never silently skip a requested check.
    subprocess.run(["docker", "info"], check=True, capture_output=True, timeout=15)
    subprocess.run(["docker", "build", "-t", IMAGE, str(ROOT)], check=True, timeout=180)


@pytest.fixture
def executor(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return DockerExecutor(workspace, tmp_path / "artifacts", IMAGE)


@pytest.mark.parametrize("fixture", ["inclusive_sum", "slugify"])
async def test_end_to_end(tmp_path, fixture):
    source = ROOT / "fixtures" / fixture
    before = snapshot(source)
    result = await run(
        source, tmp_path / "run", "Fix the bug", ScriptedProvider(demo_actions(fixture))
    )
    assert result["status"] == "succeeded", result
    assert result["baseline"]["exit_code"] == 1
    assert result["candidate"]["exit_code"] == 0
    assert result["usage"]["total_tokens"] == 0
    assert snapshot(source) == before
    assert (tmp_path / "run/patch.diff").read_text().startswith("diff --git")


async def test_command_failure_and_output_bounds(executor):
    result = await executor.shell("echo failure >&2; exit 7")
    assert result["exit_code"] == 7
    assert "failure" in result["stderr"]["text"]
    result = await executor.shell("python -c 'print(\"x\" * 30000)'")
    assert result["stdout"]["truncated"]
    assert len(result["stdout"]["text"]) == executor.output_bytes
    result = await executor.shell("python -c 'print(\"x\" * 2000000)'")
    assert result["stop_reason"] == "output_limit"
    artifacts = list(executor.artifacts.glob("*"))[-2:]
    assert all(p.stat().st_size <= 1_000_000 for p in artifacts)


async def test_paths_freshness_and_ambiguous_edits(executor):
    assert (await executor.shell("printf 'one one' > a.py; ln -s /etc/passwd escape"))[
        "exit_code"
    ] == 0
    for path in ["../a", "/etc/passwd", "escape"]:
        result = await executor.file("read_file", {"path": path, "start": 1, "count": 20})
        assert "error" in result
    edit = {"path": "a.py", "old": "one", "new": "two"}
    assert "error" in await executor.file("edit_file", edit)
    await executor.file("read_file", {"path": "a.py", "start": 1, "count": 20})
    assert "exactly once" in (await executor.file("edit_file", edit))["error"]
    await executor.file("read_file", {"path": "a.py", "start": 1, "count": 20})
    await executor.shell("true")
    assert "stale" in (await executor.file("edit_file", edit))["error"]


async def test_timeout_kills_descendants(executor):
    executor.command_seconds = 1
    result = await executor.shell("(sleep 3; echo leaked > leak) & wait")
    assert result["stop_reason"] == "timeout"
    await asyncio.sleep(3)
    assert not (executor.workspace / "leak").exists()
    assert executor.cleanup_errors == []


async def test_cancel_removes_container(executor):
    task = asyncio.create_task(executor.shell("touch started; sleep 60"))
    for _ in range(100):
        if (executor.workspace / "started").exists():
            break
        await asyncio.sleep(0.1)
    else:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        pytest.fail("Container did not start")
    name = executor.active
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    inspected = await asyncio.to_thread(
        subprocess.run, ["docker", "inspect", name], capture_output=True
    )
    assert inspected.returncode != 0
    assert executor.cleanup_errors == []


async def test_isolation(executor, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "host-only-sentinel")
    result = await executor.shell("""python - <<'PY'
import json, os, pathlib, socket
try:
    socket.create_connection(('1.1.1.1', 443), timeout=1)
    network = True
except OSError:
    network = False
try:
    pathlib.Path('/etc/agent-write').write_text('no')
    writable_root = True
except OSError:
    writable_root = False
print(json.dumps({'uid': os.getuid(), 'key': os.getenv('OPENAI_API_KEY'),
    'socket': pathlib.Path('/var/run/docker.sock').exists(),
    'network': network, 'writable_root': writable_root}))
PY""")
    assert result["exit_code"] == 0, result
    data = json.loads(result["stdout"]["text"])
    assert data["uid"] != 0
    assert data["key"] is None
    assert not any(data[k] for k in ("socket", "network", "writable_root"))
