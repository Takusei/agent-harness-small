# Coding agent harness

Milestones 1–3 provide a Python/uv CLI with an explicit agent loop, optional
read-only delegation, host-owned fixture verification, an OpenAI Responses
provider, a deterministic scripted provider, Docker tools, and patch artifacts.
It does not use an agent orchestration framework. See [PLAN.md](PLAN.md)
for later work; memory and recovery are not implemented yet.

The planned memory integration targets the standalone
[Agentic Memory MCP server](../agentic-memory-mcp/README.md). Milestone 4 will
connect through MCP with a dedicated experiment store; the server remains a
separate project with its own uv environment. Its migration is complete, but
the harness does not yet connect to it. See [PLAN.md](PLAN.md) for the milestones.

## Setup

Requires macOS/Linux, uv, and a running Docker engine. Python 3.12 is selected by
`.python-version`; host dependencies are pinned in `uv.lock`.

```sh
uv sync --locked
docker build -t agent-harness-exec:local .
uv run agent-harness --help
```

The execution image contains Python's standard library. The two fixtures use
`unittest`, so they need no package downloads. For other projects, build a trusted
image with dependencies beforehand and select it with `--image`. Runtime network
access is disabled. The base image tag is not digest-pinned yet.

## Deterministic demo (no API usage)

Run from this directory:

```sh
uv run agent-harness --repo fixtures/inclusive_sum --scripted inclusive_sum --evaluator inclusive_sum
uv run agent-harness --repo fixtures/slugify --scripted slugify --evaluator slugify
```

Each run copies the selected fixture to a fresh `runs/<id>/workspace`, runs the
baseline suite in Docker, then lists/searches/reads/edits/tests through real tools.
A final visible test run follows `finish`. Original fixtures stay deliberately
buggy and unchanged. The scripted provider emits predetermined actions; it does
not reason, adapt, or call a model.

Progress appears on stderr and the final result JSON on stdout. Set `--output` to
a **new**, separate directory to choose an artifact location. Existing run
directories are never reused. Exit codes: 0 evaluator-verified success, 1 failed,
2 invalid setup/arguments, 3 inconclusive, 130 cancelled. Without `--evaluator`,
a completed repair is inconclusive even when its visible checks pass.

## Read-only delegation (milestone 2)

Delegation is opt-in. These deterministic demonstrations use real Docker tools
and predetermined responses, with no model API calls:

```sh
uv run agent-harness --repo fixtures/inclusive_sum --scripted inclusive_sum --evaluator inclusive_sum --delegate
uv run agent-harness --repo fixtures/slugify --scripted slugify --evaluator slugify --delegate
```

Without `--delegate`, the existing single-agent mode is preserved. With it, the
parent gains a `delegate` tool. The scripted demo delegates once, then follows
its predetermined repair sequence; it does not infer a patch from the findings.
For an explicitly requested `--live` run, the same flag enables model-selected
delegation using the same model transport, with separate message lists.

The parent supplies task text, selected context, an allowlist of list/search/read
file tools, and child turn/tool/time limits. Each child receives a fresh snapshot
of the candidate workspace at delegation time, mounted **read-only** in Docker.
It cannot edit, run shell commands, or spawn another child. Only the selected
context crosses the conversation boundary; the parent transcript and baseline
output are not automatically copied. The snapshot includes the selected repository
files, so this is conversation isolation, not secrecy between repository files.

Concurrency is fixed at one child: the parent awaits the result before continuing.
`--max-children` (or `HARNESS_MAX_CHILDREN`, default 2) bounds total children per run.
`--max-turns`, `--max-tools`, and `--max-seconds` are shared across parent and
children; the delegation call itself counts as a parent tool call. Each child is
also bounded by its requested limits (at most 20 turns, 30 tools, 120 seconds).
Token usage is aggregated into the run totals, with child subtotals. There is no
separate total-token or dollar-cost cap; output tokens remain bounded per response.

Child findings include status, evidence paths, limitations, usage, and errors.
Evidence paths must exist in the child's snapshot; that does not prove the
findings correct. Failures and local timeouts return to the parent, which may
continue if the shared budget permits. Parent cancellation/deadlines cancel child
work and clean up the active container. Cleanup errors prevent run success.

CLI progress shows child IDs, lifecycle, status, and evidence. `events.jsonl`
contains child events with `task_id`, `parent_task_id`, and `delegate_call_id`.
`result.json` includes a `children` array. Each `children/<id>/` directory retains
its own `result.json`, read-only-in-container `workspace/`, and `artifacts/`.
Child results remain unverified advice; the host evaluator determines final success.
See [docs/milestone-2.md](docs/milestone-2.md) for validation.

## Independent fixture verification (milestone 3)

Select `--evaluator inclusive_sum` or `--evaluator slugify` to match the input
repository. Profiles are explicit CLI choices, never inferred from the model's
claims or the selected scripted provider. Generic repositories can still produce
a patch without an evaluator, but cannot receive verified success.

Before editing, the host evaluates a frozen baseline snapshot. After the parent
finishes and runs visible checks, it freezes and evaluates the candidate. Each
profile has six named input/output cases, including inputs outside the visible
tests. A fresh read-only Docker container calls the target function per case.
The host compares the returned value and type with an expected answer held outside
the container. Repository tests, assertions, and exit code zero alone cannot
supply evaluator success. Candidate exceptions count as failed checks; missing or
malformed results, unavailable execution, and timeouts are inconclusive.

Evaluator profiles, expected answers, and reports are not mounted into editing or
child containers and are never included in model messages. Evaluation inputs are
necessarily visible to candidate code during verification. Profiles are public
source code in this learning project, not a secret benchmark; this evaluator is
not a defense against deliberate output spoofing or a complete proof of behavior.

`verification` is `verified_success`, `verified_failure`, or `inconclusive`.
`evaluation.comparison` lists fixed checks, regressions (baseline pass → candidate
fail), and persistent failures. All candidate checks must pass; pre-existing
failures are recorded rather than silently waived. Evaluation is inside the run's
wall-time budget and uses the command timeout/output/container limits, but does
not consume model turns or model tokens. Infrastructure errors or cancellation
can leave partial reports; they never yield verified success.

To demonstrate a superficial repair (expected exit code **1**):

```sh
uv run agent-harness --repo fixtures/inclusive_sum \
  --scripted inclusive_sum_superficial --evaluator inclusive_sum
```

This predetermined patch hardcodes answers for inputs 0, 1, and 3. Visible tests
pass, but evaluator inputs 2, 10, and 100 fail. The run is `verified_failure`.

`verification/manifest.json` retains the selected profile, cases, limits, configured
image, and runner/check hashes. The runner and frozen baseline/candidate snapshots
are retained alongside per-case stdout/stderr and reports. `patch.diff` is exported
from the same frozen candidate that was evaluated. The image tag is not pinned;
reproducibility still requires the same local Docker image. See
[docs/milestone-3.md](docs/milestone-3.md) for validation and limitations.

## Live smoke run (consumes API tokens)

Edit the project-root `.env` and fill in `OPENAI_API_KEY` only when you want live
runs. `.env.example` contains the same settings without credentials. The CLI loads
only `.env` in the current working directory (it does not search parent folders).

Settings precedence is **CLI flags > .env > shell environment > built-in defaults**.
A blank `OPENAI_API_KEY` in `.env` explicitly blocks an inherited shell key.
Other blank settings use their built-in default. Values are literal, with no
`${...}` expansion. The file contains repository/task/output, model, image/check
command, turn/tool/time limits, output bytes, and model output-token limits.
`.env` is gitignored and excluded from execution workspace snapshots.

Run modes always require a CLI flag; `.env` cannot enable paid calls:

```sh
uv run agent-harness --scripted inclusive_sum --evaluator inclusive_sum  # no API calls
```

Once a key is configured, an explicitly requested live run can use:

```sh
uv run agent-harness --repo fixtures/inclusive_sum --live --model gpt-5-mini --evaluator inclusive_sum \
  --max-turns 10 --max-tools 10 --max-seconds 120
```

The key is not forwarded into containers. Model calls send the task and tool
observations to OpenAI. There is no silent fallback to scripted responses. The
SDK retries are disabled; API errors become failed runs. Actual reported input,
output, and total token counts are aggregated in `result.json`. Missing usage
(including interrupted requests) sets `usage_complete: false`; dollar cost is
unknown (`null`), not claimed to be zero. A model can exhaust its output budget
before producing a tool call; that is reported as failure.

The provider uses the documented [Responses function-calling protocol](https://developers.openai.com/api/docs/guides/function-calling)
and [Python SDK](https://developers.openai.com/api/reference/python). Opaque
continuation items remain in memory for reasoning-model compatibility, with
`store=False`; they are not written to the observable journal.

## Artifacts and reading the code

| File | Purpose |
| --- | --- |
| `result.json` | Status, baseline/final results, usage, limits, errors, limitations |
| `events.jsonl` | Stable run/task/call IDs and observable model/tool/check events |
| `patch.diff` | Git-compatible text diff against the original snapshot |
| `artifacts/*.stdout`, `*.stderr` | Bounded container output |
| `workspace/` | Disposable candidate repository for inspection |

Start with `src/agent_harness/cli.py`, then `runtime.py` for the loop and dispatch.
`providers.py` normalizes the real and scripted adapters; `schema.py` defines tool
contracts. `execution.py` controls Docker; `worker.py` performs file operations
inside it. `workspace.py` copies inputs and exports diffs without executing code.

The loop validates one tool call per turn. Invalid arguments become observations;
model failures, duplicate call IDs, or exhausted budgets terminate explicitly.
Parent tools are `list_files`, `search_files`, `read_file`, `edit_file`,
`run_command`, and `finish`, plus `delegate` when enabled. `delegation.py` owns the
child loop, result contract, and shared accounting. Edits require a fresh read hash and exactly one matching old string.
Commands and edits invalidate prior read hashes. New files can be created via a
container command; there is no separate create-file tool in this slice.

## Boundaries and limits

- Only the disposable workspace is mounted. Containers use a non-root UID, a
  read-only root filesystem, no capabilities, no network, and CPU/memory/PID limits.
  Neither the Docker socket, run artifacts, original repo nor host credentials
  are mounted. Each tool call starts a new container and removes it afterward.
- Default budgets: 16 model turns, 30 tool calls, 180 seconds overall, 30 seconds
  per command, 2,048 output tokens per model response, and 16 KB output previews.
  Stream artifacts are capped at 1 MB combined per invocation. Cleanup has a
  separate short deadline and may extend overall wall time.
- Source/export snapshots support small UTF-8 text repositories: 2,000 entries,
  1 MB per file, 20 MB total. Symlinks and special files are rejected. Common
  generated directories and `.env*`, `.aws`, `.ssh`, `.codex` are excluded; these
  exclusions are not a general secret scanner. Keep selected input repos free of
  secrets. The same exclusions apply to the exported patch.
- Docker is practical local isolation, not a guarantee against hostile kernel
  exploits. Workspace disk writes do not have a filesystem quota. Do not run
  against concurrently modified inputs or manually modify an active workspace.
- `succeeded` requires `verified_success`: completed evaluator baseline/candidate
  checks, all candidate checks passing, and passing final visible checks. Editing
  visible tests cannot alter the host's expected answers. Verification covers
  only the selected finite fixture cases, not arbitrary correctness.
- SIGINT/SIGTERM cancel active work, remove the container, and retain results.
  SIGKILL/host crashes can leave containers or an unfinished `running` result;
  there is no resume/replay/reconciliation until milestone 5.

## Verification

```sh
uv run pytest -q                         # deterministic unit tests; Docker skipped
uv run pytest --run-docker -q            # builds image, runs actual Docker tests
uv run python -m ruff check .
uv run python -m ruff format --check .
```

Requested Docker tests fail if Docker is unavailable; they never execute generated
commands on the host. Unit tests use fake executors for state transitions and an
HTTP mock for SDK serialization; neither is described as live AI. See
[docs/milestone-1.md](docs/milestone-1.md) for the recorded validation results.

## Reference attribution

Design ideas inspected in the read-only sibling `../poc/coding-agent-langchain/`:
`executor.py` (container flags and cleanup), `worker.py` (read hashes and exact
edits), `persistence.py` (observable journals), and `demo.py` (scripted tool calls).
This project independently implements orchestration and does not import or depend
on that sibling, LangChain, or LangGraph.
