# Milestone 3: independent fixture verification

Recorded 2026-10-17. This milestone makes final success depend on host-owned
checks for the two development fixtures. All validation here is deterministic;
no live model API calls were made.

## Implementation

`--evaluator inclusive_sum` and `--evaluator slugify` select explicit host-owned
profiles. Each contains six named cases, including inputs absent from visible
repository tests. Profiles and expected answers live outside the editing and
child snapshots. They are not included in model conversations.

The host freezes and evaluates the original baseline before model execution.
After `finish` and the final visible check, it freezes the candidate. Each case
runs the candidate function in a new Docker container with a read-only snapshot,
no network, credentials, or Docker socket, and the existing resource/output/time
limits. Only the invocation wrapper and input enter the evaluator container;
expected answers stay on the host. Generated code never runs on the host.

The host compares returned value and type with its expected answer. Candidate
exceptions and mismatches fail a case. Missing/malformed responses, timeouts,
output limits, unavailable execution, or uncertain cleanup are inconclusive.
A zero exit code without a structured result cannot claim success.

The overall verification outcome is one of:

- `verified_success`: evaluator baseline/candidate completed, every candidate
  case passed, and the final visible command passed.
- `verified_failure`: completed evaluation found a mismatch/exception or the
  final visible command failed.
- `inconclusive`: no evaluator was selected or verification could not complete.

Run status and verification are separate: cancellation and runtime errors retain
`cancelled`/`failed` while verification remains inconclusive. A completed repair
without an evaluator is `inconclusive` and exits 3. CLI exit 0 is reserved for
verified success; failed runs exit 1, invalid setup 2, and cancellation 130.

Comparison records fixed checks, regressions, and persistent failures. All
candidate cases must pass, including any that failed on the baseline. The final
patch uses the same frozen candidate as the evaluator.

## Reproducible artifacts

Each verified-mode run retains:

- `verification/manifest.json`: selected profile/version, expected cases, limits,
  configured image, and SHA-256 hashes of the checks and invocation wrapper.
- `verification/runner.py`: the wrapper actually sent to Docker.
- `verification/{baseline,candidate}/workspace/`: frozen source snapshots.
- `verification/{baseline,candidate}/result.json`: per-case observations and
  snapshot hashes, including partial results when interrupted.
- `verification/{baseline,candidate}/artifacts/`: bounded stdout/stderr streams.
- Top-level `result.json`: verdict, comparison, ordinary visible checks, usage,
  and a pointer to verification artifacts. Journal events show verification phases.

The verifier receives a separate copy and cannot mutate the editing workspace.
These artifacts are outside all agent mounts. Source repositories remain unchanged.

## Validation

The pre-change suite passed 51 tests. The final suite passed **76 tests in 49.93
seconds** (57 unit tests and 19 real Docker cases). Locked sync, Ruff lint,
format checks, and `git diff --check` passed. Validation commands:

```sh
uv sync --locked
uv run pytest --run-docker -q
uv run python -m ruff check .
uv run python -m ruff format --check .
git diff --check
```

Acceptance coverage includes both fixtures with delegation enabled and disabled;
visible-test tampering; a superficial patch; regressions versus pre-existing
failures; missing/malformed output; candidate exceptions; infrastructure failure;
evaluator timeout/cancellation and retained partial results; snapshot hashes;
and evaluator data being absent from agent prompts and mounted workspace paths.

Testing exposed a parent/child deadline edge: the child used the run's original
absolute deadline, while the parent created a slightly later relative deadline.
The parent now uses the same absolute deadline and checks remaining time before
each model request. The focused deadline test passed after correction.

## Retained CLI demonstrations

These runs are scripted, use real Docker execution, and consume zero API calls
and tokens. Run directories are gitignored.

| Directory under `runs/` | Visible candidate checks | Verification | Exit |
| --- | --- | --- | --- |
| `milestone-3-verified` | 3 pass | `verified_success` | 0 |
| `milestone-3-superficial` | 3 pass | `verified_failure` | 1 |
| `milestone-3-unverified` | 3 pass | `inconclusive` | 3 |

Reproduce the verified delegated case:

```sh
uv run agent-harness --repo fixtures/inclusive_sum --scripted inclusive_sum \
  --delegate --evaluator inclusive_sum
```

Reproduce the superficial repair:

```sh
uv run agent-harness --repo fixtures/inclusive_sum \
  --scripted inclusive_sum_superficial --evaluator inclusive_sum
```

The superficial patch hardcodes answers for 0, 1, and 3. It passes all visible
tests but fails evaluator cases 2, 10, and 100. Those remain recorded as persistent
failures; visible cases 1 and 3 are recorded as fixed.

## Limits and next milestone

This is finite behavioral verification for two simple functions, not a generic
test framework or proof of arbitrary correctness. The public profiles are
held outside the editing context, not secret against someone who knows this
repository. Candidate code sees evaluation inputs and shares a process with the
invocation wrapper; deliberate output spoofing/introspection remains possible.
The trusted host still owns the oracle and never accepts a candidate's claim
that tests passed as its verdict.

Replay requires the same Docker image. The manifest records the configured image
name, not an immutable image digest. Arbitrary dependencies, fixture plugins,
behavioral performance limits, and hostile-program evaluation are out of scope.
The command and run deadlines include evaluator execution; cleanup may extend
wall time. SIGKILL recovery remains milestone 5.

The scripted overfitting example validates the evaluator boundary; it does not
measure whether real models overfit or whether delegation improves quality.
The next milestone is MCP memory integration with a dedicated experiment store.
No sibling repositories, memory stores, dependencies, or credentials were changed.
