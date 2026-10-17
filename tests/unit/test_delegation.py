import asyncio
import json
from copy import deepcopy

import pytest

from agent_harness.providers import ScriptedProvider, investigation_request
from agent_harness.runtime import run
from agent_harness.schema import Limits

FINISH = ("finish", {"summary": "Parent finished", "limitations": []})
REPORT = (
    "finish",
    {
        "findings": "Inspect boundary",
        "evidence_paths": ["module.py"],
        "limitations": ["Unverified"],
    },
)


class Executor:
    instances = []

    def __init__(self, workspace, *args, read_only=False):
        self.workspace = workspace
        self.read_only = read_only
        self.cleanup_errors = []
        self.files = []
        self.instances.append(self)

    async def shell(self, command):
        assert not self.read_only
        return {"exit_code": 0, "stop_reason": None}

    async def file(self, name, arguments):
        self.files.append(name)
        return {"text": "a = 1"}


class RecordingProvider(ScriptedProvider):
    def __init__(self, actions):
        super().__init__(actions)
        self.requests = []

    async def respond(self, messages, tools, timeout):
        self.requests.append(deepcopy((messages, tools)))
        response = await super().respond(messages, tools, timeout)
        response.usage = dict(input_tokens=2, output_tokens=1, total_tokens=3)
        return response


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "source"
    path.mkdir()
    (path / "module.py").write_text("a = 1\n")
    Executor.instances.clear()
    return path


async def invoke(source, tmp_path, child, request=None, parent=None, **kwargs):
    request = request or investigation_request()
    return await run(
        source,
        tmp_path / "run",
        "PRIVATE_PARENT_CONTEXT",
        parent or ScriptedProvider([("delegate", request), FINISH]),
        child_provider_factory=lambda: child,
        executor_factory=Executor,
        **kwargs,
    )


def events(tmp_path):
    return [json.loads(x) for x in (tmp_path / "run/events.jsonl").read_text().splitlines()]


async def test_context_snapshot_events_and_usage(source, tmp_path):
    child = RecordingProvider(
        [("read_file", {"path": "module.py", "start": 1, "count": 5}), REPORT]
    )
    parent = RecordingProvider([("delegate", investigation_request()), FINISH])
    result = await invoke(source, tmp_path, child, parent=parent)
    assert result["status"] == "inconclusive"
    report = result["children"][0]
    assert report["status"] == "succeeded"
    assert result["model_calls"] == result["tool_calls"] == 4
    assert result["usage"]["total_tokens"] == 12
    assert report["usage"]["total_tokens"] == 6
    assert report["parent_task_id"] == result["task_id"]
    assert "PRIVATE_PARENT_CONTEXT" not in json.dumps(child.requests)
    assert "Baseline check result" not in json.dumps(child.requests)
    assert len(child.requests[0][0]) == 2
    assert {t["name"] for t in child.requests[0][1]} == {
        "list_files",
        "search_files",
        "read_file",
        "finish",
    }
    parent_executor, child_executor = Executor.instances
    assert child_executor.read_only and not parent_executor.read_only
    assert child_executor.workspace != parent_executor.workspace
    assert (child_executor.workspace / "module.py").read_text() == "a = 1\n"
    (parent_executor.workspace / "module.py").write_text("changed")
    assert (child_executor.workspace / "module.py").read_text() == "a = 1\n"
    assert (source / "module.py").read_text() == "a = 1\n"
    assert report["findings"] in json.dumps(parent.requests[-1])
    child_events = [e for e in events(tmp_path) if e["task_id"] == report["task_id"]]
    assert child_events[0]["type"] == "child_started"
    assert child_events[-1]["type"] == "child_finished"
    assert all(e["parent_task_id"] == result["task_id"] for e in child_events)
    assert (tmp_path / "run" / report["workspace"]).is_dir()


@pytest.mark.parametrize("name", ["edit_file", "run_command", "delegate", "search_files"])
async def test_denied_tools_never_dispatched(source, tmp_path, name):
    request = {**investigation_request(), "allowed_tools": ["read_file"]}
    child = RecordingProvider([(name, {}), REPORT])
    result = await invoke(source, tmp_path, child, request=request)
    assert result["children"][0]["status"] == "succeeded"
    assert Executor.instances[-1].files == []
    assert "Tool denied" in json.dumps(child.requests[-1])


async def test_bad_evidence_and_malformed_arguments_are_observations(source, tmp_path):
    child = RecordingProvider(
        [
            ("read_file", {"path": "module.py", "start": "1", "count": 5}),
            ("finish", {**REPORT[1], "evidence_paths": ["../escape"]}),
            REPORT,
        ]
    )
    result = await invoke(source, tmp_path, child)
    assert result["children"][0]["status"] == "succeeded"
    assert Executor.instances[-1].files == []
    assert "Evidence paths" in json.dumps(child.requests[-1])


@pytest.mark.parametrize("limits", [Limits(turns=2), Limits(tool_calls=1)])
async def test_shared_budget_stops_parent_and_child(source, tmp_path, limits):
    result = await invoke(source, tmp_path, ScriptedProvider([REPORT]), limits=limits)
    assert result["status"] == "failed"
    assert "budget exhausted" in result["error"]
    assert result["model_calls"] <= limits.turns
    assert result["tool_calls"] <= limits.tool_calls


@pytest.mark.parametrize("budget", [{"turns": 1}, {"tool_calls": 1}])
async def test_child_budget_failure_returned_to_parent(source, tmp_path, budget):
    child = ScriptedProvider([("list_files", {"query": ""}), REPORT])
    result = await invoke(source, tmp_path, child, request={**investigation_request(), **budget})
    assert result["status"] == "inconclusive"
    assert result["children"][0]["status"] == "failed"
    assert "budget exhausted" in result["children"][0]["error"]


class Hanging:
    def __init__(self):
        self.started = asyncio.Event()

    async def respond(self, *args):
        self.started.set()
        await asyncio.Event().wait()


async def test_child_timeout_parent_can_recover(source, tmp_path):
    result = await invoke(
        source, tmp_path, Hanging(), request={**investigation_request(), "seconds": 0.02}
    )
    assert result["status"] == "inconclusive"
    assert result["children"][0]["status"] == "failed"
    assert "elapsed time" in result["children"][0]["error"]
    assert not result["usage_complete"]


async def test_cancel_during_child_preserves_both_results(source, tmp_path):
    child = Hanging()
    task = asyncio.create_task(invoke(source, tmp_path, child))
    await asyncio.wait_for(child.started.wait(), 2)
    task.cancel()
    result = await task
    assert result["status"] == "cancelled"
    assert result["children"][0]["status"] == "cancelled"
    assert not result["usage_complete"]
    assert events(tmp_path)[-1]["type"] == "run_finished"
    child_result = next((tmp_path / "run/children").glob("*/result.json"))
    assert json.loads(child_result.read_text())["status"] == "cancelled"


async def test_parent_deadline_cancels_child(source, tmp_path):
    result = await invoke(source, tmp_path, Hanging(), limits=Limits(seconds=0.03))
    assert result["status"] == "failed"
    assert result["children"][0]["status"] in {"cancelled", "failed"}
    assert not result["usage_complete"]


async def test_child_count_cap_and_fresh_provider(source, tmp_path):
    made = []

    def factory():
        child = ScriptedProvider([REPORT])
        made.append(child)
        return child

    parent = ScriptedProvider([("delegate", investigation_request())] * 3 + [FINISH])
    result = await run(
        source,
        tmp_path / "run",
        "repair",
        parent,
        child_provider_factory=factory,
        executor_factory=Executor,
    )
    assert result["status"] == "inconclusive"
    assert len(result["children"]) == len(made) == 2
    assert "Child count budget exhausted" in json.dumps(events(tmp_path))
    lifecycle = [e["type"] for e in events(tmp_path) if e["type"].startswith("child_")]
    assert lifecycle == ["child_started", "child_finished"] * 2


async def test_disabled_delegation_rejected(source, tmp_path):
    result = await run(
        source,
        tmp_path / "run",
        "repair",
        ScriptedProvider([("delegate", investigation_request()), FINISH]),
        executor_factory=Executor,
    )
    assert result["status"] == "inconclusive"
    assert result["children"] == []
    assert "Unknown tool" in json.dumps(events(tmp_path))


async def test_child_provider_failure_and_cleanup_failure(source, tmp_path, monkeypatch):
    async def failed_file(self, *args):
        self.cleanup_errors.append("Removal failed")
        raise OSError("Container failure")

    monkeypatch.setattr(Executor, "file", failed_file)
    result = await invoke(source, tmp_path, ScriptedProvider([("list_files", {"query": ""})]))
    assert result["status"] == "failed"
    assert result["cleanup_errors"] == ["Removal failed"]
    assert result["children"][0]["status"] == "failed"
