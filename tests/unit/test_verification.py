import json

import pytest

from agent_harness.providers import ScriptedProvider
from agent_harness.runtime import run
from agent_harness.schema import Limits
from agent_harness.verification import FixtureVerifier, classify, compare, snapshot_digest


def report(status, cases):
    return {"status": status, "cases": [{"id": k, "status": v} for k, v in cases.items()]}


@pytest.mark.parametrize(
    "candidate, expected",
    [
        (report("pass", {"a": "pass", "b": "pass"}), "verified_success"),
        (report("fail", {"a": "fail", "b": "pass"}), "verified_failure"),
        (report("inconclusive", {"a": "pass", "b": "inconclusive"}), "inconclusive"),
        (None, "inconclusive"),
    ],
)
def test_comparison(candidate, expected):
    baseline = report("fail", {"a": "pass", "b": "fail"})
    outcome, changes = compare(baseline, candidate, {"exit_code": 0, "stop_reason": None})
    assert outcome == expected
    if expected == "verified_failure":
        assert changes == {"fixed": ["b"], "regressions": ["a"], "persistent_failures": []}


@pytest.mark.parametrize(
    "exit_code, stop_reason, raw, status",
    [
        (0, None, b'{"value": 3}', "returned"),
        (0, None, b'{"error": "TypeError"}', "fail"),
        (1, None, b"", "fail"),
        (125, None, b"", "inconclusive"),
        (-9, "timeout", b"", "inconclusive"),
        (0, "output_limit", b'{"value": 3}', "inconclusive"),
        (0, None, b"", "inconclusive"),
        (0, None, b'{"status": "pass"}', "inconclusive"),
        (0, None, b'{"value": 3}\n{"value": 3}', "inconclusive"),
    ],
)
def test_missing_or_untrusted_execution_result(exit_code, stop_reason, raw, status):
    assert classify({"exit_code": exit_code, "stop_reason": stop_reason}, raw)[0] == status


def test_baseline_inconclusive_or_visible_failure_prevents_success():
    success = report("pass", {"a": "pass"})
    assert (
        compare(report("inconclusive", {}), success, {"exit_code": 0, "stop_reason": None})[0]
        == "inconclusive"
    )
    assert compare(success, success, {"exit_code": 1, "stop_reason": None})[0] == "verified_failure"
    assert (
        compare(success, success, {"exit_code": 0, "stop_reason": "timeout"})[0] == "inconclusive"
    )


def test_snapshot_hash_covers_contents_modes_and_names():
    files = {"b": (b"two", 0o644), "a": (b"one", 0o644)}
    assert snapshot_digest(files) == snapshot_digest(dict(reversed(list(files.items()))))
    for changed in [
        {"b": (b"two", 0o644), "a": (b"one!", 0o644)},
        {"b": (b"two", 0o644), "a": (b"one", 0o755)},
        {"b": (b"two", 0o644), "x": (b"one", 0o644)},
    ]:
        assert snapshot_digest(files) != snapshot_digest(changed)


async def test_infrastructure_failure_preserves_evidence(tmp_path):
    class MissingDocker:
        def __init__(self, *args, **kwargs):
            self.cleanup_errors = []

        async def run(self, *args):
            raise OSError("Docker unavailable")

    verifier = FixtureVerifier("inclusive_sum", tmp_path, "missing", Limits(), MissingDocker)
    result = await verifier.evaluate({"math_utils.py": (b"", 0o644)}, "baseline")
    assert result["status"] == "inconclusive"
    assert "Docker unavailable" in result["error"]
    stored = json.loads((tmp_path / "verification/baseline/result.json").read_text())
    assert stored == result
    assert (tmp_path / "verification/manifest.json").is_file()


async def test_harness_source_cannot_expose_evaluator(tmp_path):
    from pathlib import Path

    source = Path(__file__).resolve().parents[2]
    with pytest.raises(ValueError, match="evaluator implementation"):
        await run(
            source, tmp_path / "run", "repair", ScriptedProvider([]), evaluator="inclusive_sum"
        )


async def test_cancelled_verifier_saves_partial_result(tmp_path):
    import asyncio

    started = asyncio.Event()

    class HangingExecutor:
        def __init__(self, *args, **kwargs):
            self.cleanup_errors = []

        async def run(self, *args):
            started.set()
            await asyncio.Event().wait()

    verifier = FixtureVerifier("inclusive_sum", tmp_path, "test", Limits(), HangingExecutor)
    task = asyncio.create_task(verifier.evaluate({"math_utils.py": (b"", 0o644)}, "candidate"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    result = json.loads((tmp_path / "verification/candidate/result.json").read_text())
    assert result["status"] == "inconclusive"
    assert "cancelled" in result["error"]
