"""Real Docker tests. Never substitute host execution when Docker is unavailable."""

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from agent_harness.execution import DockerExecutor
from agent_harness.providers import (
    ScriptedProvider,
    demo_actions,
    investigation_request,
    investigator_actions,
)
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
@pytest.mark.parametrize("delegated", [False, True])
async def test_end_to_end(tmp_path, fixture, delegated):
    source = ROOT / "fixtures" / fixture
    before = snapshot(source)
    actions = ([("delegate", investigation_request())] if delegated else []) + demo_actions(fixture)
    result = await run(
        source,
        tmp_path / "run",
        "Fix the bug",
        ScriptedProvider(actions),
        evaluator=fixture,
        child_provider_factory=(lambda: ScriptedProvider(investigator_actions(fixture)))
        if delegated
        else None,
    )
    assert result["status"] == "succeeded", result
    assert result["baseline"]["exit_code"] == 1
    assert result["candidate"]["exit_code"] == 0
    assert result["usage"]["total_tokens"] == 0
    assert snapshot(source) == before
    assert (tmp_path / "run/patch.diff").read_text().startswith("diff --git")
    if delegated:
        child = result["children"][0]
        assert child["status"] == "succeeded"
        assert snapshot(tmp_path / "run" / child["workspace"]) == before
        assert result["model_calls"] == result["tool_calls"] == 10


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


async def test_child_mount_is_really_read_only(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "module.py").write_text("a = 1\n")
    child = DockerExecutor(workspace, tmp_path / "artifacts", IMAGE, read_only=True)
    assert ",readonly" in " ".join(child.docker_args("test"))
    # Deliberately bypass model tool dispatch to test the Docker boundary itself.
    result = await child.shell("echo changed > module.py; touch forbidden")
    assert result["exit_code"] != 0
    assert "Read-only file system" in result["stderr"]["text"]
    assert not (workspace / "forbidden").exists()
    assert (workspace / "module.py").read_text() == "a = 1\n"
    await child.file("read_file", {"path": "module.py", "start": 1, "count": 10})
    result = await child.file("edit_file", {"path": "module.py", "old": "1", "new": "2"})
    assert "error" in result
    assert (workspace / "module.py").read_text() == "a = 1\n"


@pytest.mark.parametrize("stop", ["cancel", "timeout"])
async def test_active_child_container_cleanup(tmp_path, stop):
    started = asyncio.Event()
    children = []

    class SlowReadExecutor(DockerExecutor):
        async def file(self, name, arguments):
            if not self.read_only:
                return await super().file(name, arguments)
            children.append(self)
            # Only a deterministic test injects this command, inside Docker.
            # The investigator itself has no command tool.
            task = asyncio.create_task(self.shell("echo child-started; sleep 60"))
            try:
                for _ in range(100):
                    if self.active:
                        inspected = await asyncio.to_thread(
                            subprocess.run,
                            ["docker", "inspect", "--format={{.State.Running}}", self.active],
                            capture_output=True,
                            timeout=2,
                        )
                        if inspected.returncode == 0 and inspected.stdout.strip() == b"true":
                            started.set()
                            break
                    await asyncio.sleep(0.02)
                return await task
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    request = {**investigation_request(), "seconds": 2.0 if stop == "timeout" else 30.0}
    parent = ScriptedProvider([("delegate", request), *demo_actions("inclusive_sum")])
    task = asyncio.create_task(
        run(
            ROOT / "fixtures/inclusive_sum",
            tmp_path / "run",
            "repair",
            parent,
            executor_factory=SlowReadExecutor,
            child_provider_factory=lambda: ScriptedProvider(
                [("read_file", {"path": "math_utils.py", "start": 1, "count": 10})]
            ),
        )
    )
    try:
        await asyncio.wait_for(started.wait(), 10)
        name = children[0].active
        if stop == "cancel":
            task.cancel()
        result = await asyncio.wait_for(task, 20)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert result["status"] == ("cancelled" if stop == "cancel" else "inconclusive")
    assert result["children"][0]["status"] == ("cancelled" if stop == "cancel" else "failed")
    assert result["cleanup_errors"] == []
    inspected = await asyncio.to_thread(
        subprocess.run,
        ["docker", "inspect", name],
        capture_output=True,
    )
    assert inspected.returncode != 0
    assert children[0].active is None


async def test_superficial_patch_passes_visible_but_fails_evaluator(tmp_path):
    result = await run(
        ROOT / "fixtures/inclusive_sum",
        tmp_path / "run",
        "repair",
        ScriptedProvider(demo_actions("inclusive_sum_superficial")),
        evaluator="inclusive_sum",
    )
    assert result["candidate"]["exit_code"] == 0
    assert result["status"] == "failed"
    assert result["verification"] == "verified_failure"
    cases = {c["id"]: c["status"] for c in result["evaluation"]["candidate"]["cases"]}
    assert cases == {
        "zero": "pass",
        "one": "pass",
        "three": "pass",
        "two": "fail",
        "ten": "fail",
        "hundred": "fail",
    }
    assert result["evaluation"]["comparison"]["fixed"] == ["one", "three"]
    assert result["evaluation"]["comparison"]["persistent_failures"] == ["two", "ten", "hundred"]


async def test_editing_visible_tests_cannot_make_verified_success(tmp_path):
    actions = [
        (
            "run_command",
            {
                "command": "printf 'import unittest\nclass Tests(unittest.TestCase):\n"
                "    def test_fake(self): self.assertTrue(True)\n' > test_math_utils.py"
            },
        ),
        ("finish", {"summary": "Tests pass", "limitations": []}),
    ]
    result = await run(
        ROOT / "fixtures/inclusive_sum",
        tmp_path / "run",
        "repair",
        ScriptedProvider(actions),
        evaluator="inclusive_sum",
    )
    assert result["candidate"]["exit_code"] == 0
    assert result["verification"] == "verified_failure"
    assert result["status"] == "failed"


async def test_regressions_are_distinct_from_existing_failures(tmp_path):
    actions = demo_actions("inclusive_sum")
    actions[3][1]["new"] = "range(n + 1) if n else [1]"
    result = await run(
        ROOT / "fixtures/inclusive_sum",
        tmp_path / "run",
        "repair",
        ScriptedProvider(actions),
        evaluator="inclusive_sum",
    )
    assert result["verification"] == "verified_failure"
    comparison = result["evaluation"]["comparison"]
    assert comparison["regressions"] == ["zero"]
    assert comparison["fixed"] == ["one", "three", "two", "ten", "hundred"]
    assert comparison["persistent_failures"] == []


async def test_evaluator_data_not_exposed_to_agent(tmp_path):
    class Observed(ScriptedProvider):
        async def respond(self, messages, tools, timeout):
            serialized = json.dumps(messages)
            assert "checks_sha256" not in serialized
            assert "hundred" not in serialized
            assert "verification_finished" not in serialized
            return await super().respond(messages, tools, timeout)

    probe = (
        "run_command",
        {
            "command": "test ! -e /workspace/verification && test ! -e /verification "
            "&& test ! -e /workspace/../verification && echo evaluator-not-mounted"
        },
    )
    result = await run(
        ROOT / "fixtures/inclusive_sum",
        tmp_path / "run",
        "repair",
        Observed([probe, *demo_actions("inclusive_sum")]),
        evaluator="inclusive_sum",
    )
    assert result["verification"] == "verified_success"
    journal = (tmp_path / "run/events.jsonl").read_text()
    assert "evaluator-not-mounted" in journal
    manifest = json.loads((tmp_path / "run/verification/manifest.json").read_text())
    assert manifest["checks_sha256"] and manifest["runner_sha256"]
    assert (tmp_path / "run/verification/candidate/workspace/math_utils.py").read_bytes() == (
        tmp_path / "run/workspace/math_utils.py"
    ).read_bytes()


@pytest.mark.parametrize(
    "body, outcome",
    [
        ("import os; os._exit(0)", "inconclusive"),
        ("raise ValueError('bad candidate')", "verified_failure"),
    ],
)
async def test_candidate_crash_cannot_claim_success(tmp_path, body, outcome):
    actions = [
        ("run_command", {"command": f"printf '%s\\n' \"{body}\" > math_utils.py"}),
        ("finish", {"summary": "Claimed success", "limitations": []}),
    ]
    # Keep visible command independent to exercise evaluator classification alone.
    result = await run(
        ROOT / "fixtures/inclusive_sum",
        tmp_path / "run",
        "repair",
        ScriptedProvider(actions),
        evaluator="inclusive_sum",
        check="true",
    )
    assert result["verification"] == outcome
    assert result["status"] != "succeeded"


async def test_verifier_timeout_is_inconclusive_and_preserves_snapshot(tmp_path):
    from agent_harness.schema import Limits
    from agent_harness.verification import FixtureVerifier

    verifier = FixtureVerifier("inclusive_sum", tmp_path, IMAGE, Limits(command_seconds=0.3))
    files = {"math_utils.py": (b"def inclusive_sum(n):\n    while True: pass\n", 0o644)}
    result = await verifier.evaluate(files, "candidate")
    assert result["status"] == "inconclusive"
    assert all(c["execution"]["stop_reason"] == "timeout" for c in result["cases"])
    assert verifier.cleanup_errors == []
    assert snapshot(tmp_path / "verification/candidate/workspace") == files
