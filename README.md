# Coding agent harness

Milestone 1 is a Python/uv CLI with an explicit single-agent loop, an OpenAI
Responses provider, a deterministic scripted provider, Docker tools, and patch
artifacts. It does not use an agent orchestration framework. See [PLAN.md](PLAN.md)
for later work; delegation, independent evaluation, memory and recovery are not
implemented yet.

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
uv run agent-harness --repo fixtures/inclusive_sum --scripted inclusive_sum
uv run agent-harness --repo fixtures/slugify --scripted slugify
```

Each run copies the selected fixture to a fresh `runs/<id>/workspace`, runs the
baseline suite in Docker, then lists/searches/reads/edits/tests through real tools.
A final visible test run follows `finish`. Original fixtures stay deliberately
buggy and unchanged. The scripted provider emits predetermined actions; it does
not reason, adapt, or call a model.

Progress appears on stderr and the final result JSON on stdout. Set `--output` to
a **new**, separate directory to choose an artifact location. Existing run
directories are never reused. Exit codes: 0 completed with passing visible checks,
1 failed, 2 invalid setup/arguments, 130 cancelled.

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
uv run agent-harness --scripted inclusive_sum   # uses .env defaults, no API calls
```

Once a key is configured, an explicitly requested live run can use:

```sh
uv run agent-harness --repo fixtures/inclusive_sum --live --model gpt-5-mini \
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
Tools are `list_files`, `search_files`, `read_file`, `edit_file`, `run_command`, and
`finish`. Edits require a fresh read hash and exactly one matching old string.
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
- `succeeded` means the loop finished and the final **visible** check command
  returned zero. `verification` stays `not_independently_verified`: the agent can
  modify visible tests, and a command can pass without exercising useful checks.
  Evaluator-owned checks belong to milestone 3.
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
