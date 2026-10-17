"""Host-owned fixture oracles; expected answers never enter agent conversations or mounts."""

import asyncio
import hashlib
import json
from pathlib import Path

from agent_harness import verification_worker
from agent_harness.execution import DockerExecutor
from agent_harness.workspace import copy_snapshot

# Deliberately outside the editable fixture repositories. These are development
# checks, not a secret benchmark or a guarantee against adversarial programs.
PROFILES = {
    "inclusive_sum": {
        "module": "math_utils",
        "function": "inclusive_sum",
        "cases": [
            {"id": "zero", "args": [0], "expected": 0},
            {"id": "one", "args": [1], "expected": 1},
            {"id": "three", "args": [3], "expected": 6},
            {"id": "two", "args": [2], "expected": 3},
            {"id": "ten", "args": [10], "expected": 55},
            {"id": "hundred", "args": [100], "expected": 5050},
        ],
    },
    "slugify": {
        "module": "text_utils",
        "function": "slugify",
        "cases": [
            {"id": "empty", "args": [""], "expected": ""},
            {"id": "single", "args": ["HELLO"], "expected": "hello"},
            {"id": "spaces", "args": ["  Hello   World  "], "expected": "hello-world"},
            {"id": "tabs", "args": ["Hello\tWorld"], "expected": "hello-world"},
            {"id": "mixed", "args": [" A\n\tB\r C "], "expected": "a-b-c"},
            {"id": "unicode_space", "args": ["A\u2003B"], "expected": "a-b"},
        ],
    },
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def snapshot_digest(files):
    return digest(
        json.dumps(
            [[name, digest(data), mode] for name, (data, mode) in sorted(files.items())],
            separators=(",", ":"),
        ).encode()
    )


def classify(execution, raw):
    if execution["stop_reason"] or execution["exit_code"] in (125, 126, 127):
        return "inconclusive", "Execution interrupted or unavailable", None
    if execution["exit_code"] != 0:
        return "fail", "Candidate process exited unsuccessfully", None
    try:
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) not in ({"value"}, {"error"}):
            raise ValueError("Unexpected response shape")
    except (ValueError, UnicodeError):
        return "inconclusive", "Missing or malformed evaluator response", None
    if "error" in value:
        return "fail", "Candidate raised an exception", value
    return "returned", None, value


def compare(baseline, candidate, visible):
    comparison = {"fixed": [], "regressions": [], "persistent_failures": []}
    if baseline and candidate:
        previous = {c["id"]: c["status"] for c in baseline["cases"]}
        for case in candidate["cases"]:
            old, new = previous.get(case["id"]), case["status"]
            if old == "fail" and new == "pass":
                comparison["fixed"].append(case["id"])
            elif old == "pass" and new == "fail":
                comparison["regressions"].append(case["id"])
            elif old == new == "fail":
                comparison["persistent_failures"].append(case["id"])
    if not baseline or not candidate or not visible:
        return "inconclusive", comparison
    if baseline["status"] == "inconclusive" or candidate["status"] == "inconclusive":
        return "inconclusive", comparison
    if visible["stop_reason"] or visible["exit_code"] in (125, 126, 127):
        return "inconclusive", comparison
    if candidate["status"] == "fail" or visible["exit_code"] != 0:
        return "verified_failure", comparison
    return "verified_success", comparison


class FixtureVerifier:
    def __init__(self, profile, output, image, limits, executor_factory=DockerExecutor):
        self.profile = json.loads(json.dumps(PROFILES[profile]))
        self.output = output / "verification"
        self.output.mkdir()
        self.image, self.limits = image, limits
        self.executor_factory = executor_factory
        self.cleanup_errors = []
        self.runner = Path(verification_worker.__file__).read_bytes()
        self.manifest = {
            "profile": profile,
            "version": 1,
            "image": image,
            "limits": limits.model_dump(),
            "checks": self.profile,
            "runner_sha256": digest(self.runner),
            "checks_sha256": digest(json.dumps(self.profile, sort_keys=True).encode()),
        }
        (self.output / "manifest.json").write_text(json.dumps(self.manifest, indent=2) + "\n")
        (self.output / "runner.py").write_bytes(self.runner)

    async def evaluate(self, files, phase):
        directory = self.output / phase
        copy_snapshot(files, directory / "workspace")
        report = {
            "status": "inconclusive",
            "snapshot_sha256": snapshot_digest(files),
            "cases": [],
            "error": None,
        }
        executor = self.executor_factory(
            directory / "workspace",
            directory / "artifacts",
            self.image,
            self.limits.output_bytes,
            self.limits.command_seconds,
            read_only=True,
        )
        try:
            for case in self.profile["cases"]:
                request = {
                    "module": self.profile["module"],
                    "function": self.profile["function"],
                    "args": case["args"],
                }
                execution = await executor.run(["-I", "-", json.dumps(request)], self.runner)
                artifact = execution.get("stdout", {}).get("artifact")
                raw = (directory / "artifacts" / artifact).read_bytes() if artifact else b""
                status, error, actual = classify(execution, raw)
                if status == "returned":
                    value = actual["value"]
                    status = (
                        "pass"
                        if type(value) is type(case["expected"]) and value == case["expected"]
                        else "fail"
                    )
                report["cases"].append(
                    {
                        **case,
                        "status": status,
                        "actual": actual,
                        "error": error,
                        "execution": execution,
                    }
                )
            statuses = {c["status"] for c in report["cases"]}
            report["status"] = (
                "inconclusive"
                if "inconclusive" in statuses or not statuses
                else "fail"
                if "fail" in statuses
                else "pass"
            )
        except asyncio.CancelledError:
            report["error"] = "Verification cancelled or run deadline exhausted"
            raise
        except (OSError, ValueError, TimeoutError) as exc:
            report["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            self.cleanup_errors.extend(executor.cleanup_errors)
            if executor.cleanup_errors:
                report.update(status="inconclusive", error="Evaluator container cleanup failed")
            (directory / "result.json").write_text(json.dumps(report, indent=2) + "\n")
        return report
