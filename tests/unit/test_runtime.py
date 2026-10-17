import asyncio
import json

import pytest

from agent_harness.providers import Call, ScriptedProvider, Turn
from agent_harness.runtime import run
from agent_harness.schema import Limits


class FakeExecutor:
    """No commands executed; only tests host orchestration."""

    instances = []

    def __init__(self, *args):
        self.commands = []
        self.files = []
        self.cleanup_errors = []
        self.instances.append(self)

    async def shell(self, command):
        self.commands.append(command)
        return {"exit_code": 0, "stop_reason": None, "stdout": {}, "stderr": {}}

    async def file(self, name, arguments):
        self.files.append((name, arguments))
        return {"matches": []}


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "source"
    path.mkdir()
    (path / "module.py").write_text("a = 1\n")
    return path


async def invoke(source, tmp_path, provider, **kwargs):
    return await run(
        source, tmp_path / "run", "repair", provider, executor_factory=FakeExecutor, **kwargs
    )


FINISH = ("finish", {"summary": "Done", "limitations": []})


async def test_finish_and_observable_journal(source, tmp_path):
    result = await invoke(source, tmp_path, ScriptedProvider([FINISH]))
    assert result["status"] == "inconclusive"
    assert result["verification"] == "inconclusive"
    assert result["usage"]["total_tokens"] == 0
    assert (source / "module.py").read_text() == "a = 1\n"
    events = [json.loads(line) for line in (tmp_path / "run/events.jsonl").read_text().splitlines()]
    assert events[-1]["type"] == "run_finished"
    assert len({e["run_id"] for e in events}) == 1
    assert len({e["task_id"] for e in events}) == 1


@pytest.mark.parametrize(
    "action",
    [
        ("unknown", {}),
        ("read_file", {"path": "x", "start": "1", "count": 5}),
        ("run_command", {"command": "echo", "extra": True}),
    ],
)
async def test_invalid_arguments_are_observations(source, tmp_path, action):
    result = await invoke(source, tmp_path, ScriptedProvider([action, FINISH]))
    assert result["status"] == "inconclusive"
    assert FakeExecutor.instances[-1].files == []
    assert len(FakeExecutor.instances[-1].commands) == 2  # baseline and final only
    events = (tmp_path / "run/events.jsonl").read_text()
    assert '"error"' in events


@pytest.mark.parametrize("limits", [Limits(turns=1), Limits(tool_calls=1)])
async def test_budgets(source, tmp_path, limits):
    result = await invoke(
        source, tmp_path, ScriptedProvider([("list_files", {"query": ""}), FINISH]), limits=limits
    )
    assert result["status"] == "failed"
    assert "budget exhausted" in result["error"]


class HangingProvider:
    label = "test"

    async def respond(self, *args):
        await asyncio.Event().wait()


async def test_wall_timeout(source, tmp_path):
    result = await invoke(source, tmp_path, HangingProvider(), limits=Limits(seconds=0.02))
    assert result["status"] == "failed"
    assert "Elapsed time" in result["error"]
    assert result["usage_complete"] is False


async def test_cancellation_preserves_result(source, tmp_path):
    task = asyncio.create_task(invoke(source, tmp_path, HangingProvider()))
    await asyncio.sleep(0.02)
    task.cancel()
    result = await task
    assert result["status"] == "cancelled"
    assert result["usage_complete"] is False
    assert (tmp_path / "run/result.json").is_file()


async def test_provider_exception(source, tmp_path):
    result = await invoke(source, tmp_path, ScriptedProvider([]))
    assert result["status"] == "failed"
    assert "Script exhausted" in result["error"]


async def test_malformed_json(source, tmp_path):
    class Malformed:
        label = "test"

        async def respond(self, messages, *args):
            if len(messages) == 3:
                return Turn([Call("bad", "read_file", "{")])
            return Turn([Call("done", "finish", json.dumps(FINISH[1]))])

    result = await invoke(source, tmp_path, Malformed())
    assert result["status"] == "inconclusive"
    assert FakeExecutor.instances[-1].files == []


async def test_failed_final_checks_not_success(source, tmp_path):
    class FailedChecks(FakeExecutor):
        async def shell(self, command):
            return {"exit_code": 1, "stop_reason": None}

    result = await run(
        source,
        tmp_path / "run",
        "repair",
        ScriptedProvider([FINISH]),
        executor_factory=FailedChecks,
    )
    assert result["status"] == "failed"
    assert result["baseline"]["exit_code"] == 1
    assert result["candidate"]["exit_code"] == 1


async def test_output_inside_source_rejected(source):
    with pytest.raises(ValueError, match="separate"):
        await run(source, source / "runs", "repair", ScriptedProvider([]))
