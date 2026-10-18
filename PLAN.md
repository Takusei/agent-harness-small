# Coding Agent Harness — implementation plan

## Purpose and agreed direction

Build an independent, local-first side project to learn agent runtime engineering
and create a demonstrable portfolio artifact. The user wants to implement the
mechanisms behind subagents, sandbox execution, memory, and recovery rather than
configure a framework that already supplies them.

Use **uv**. Implement in Python, provisionally Python 3.12. Verify current SDK
interfaces and compatible versions when initializing, then lock dependencies.
Do not use LangChain Deep Agents or delegate orchestration to LangGraph. A model
SDK handles model transport; our code owns the loop, task state, delegation,
tool dispatch, budgets, and recovery. Use a provider adapter so deterministic
scripted responses and a real model exercise the same runtime.

No public launch is required. Initial deliverables are a CLI, reproducible
fixtures, readable traces, and an evaluation report. Milestones 1–3 are implemented;
see [README.md](README.md) and the validation records for
[milestone 1](docs/milestone-1.md), [milestone 2](docs/milestone-2.md), and
[milestone 3](docs/milestone-3.md).
Milestones 4–6 remain planned. Recorded results are historical evidence, not a
claim that checks were rerun during this documentation update.

## First supported workflow

Given a small Python repository and a bug description:

1. Copy the explicitly selected repository into a disposable workspace.
2. Run baseline checks and inspect the failure.
3. Optionally delegate investigation to a child with isolated conversation context.
4. Make a bounded patch in the disposable workspace.
5. Verify it against existing tests and evaluator-owned checks.
6. Return a patch, artifacts, test results, and limitations for human review.
7. Optionally record the observable completed trace in the memory service.

The source repository is unchanged. No automatic PR creation or external writes.
Begin with Python bug repair; dependency upgrades and multi-repository changes
are later possibilities, not requirements for v1.

## Existing work to reuse thoughtfully

- `../../../learning/coding-agent-langchain/`: inspect its Docker executor, file guards,
  CLI, checkpoint/journal design, and deterministic demo. Reuse small appropriate
  utilities with attribution where needed; do not copy the whole LangGraph runtime.
- `../agentic-memory-mcp/`: standalone MCP memory server. Read its `AGENTS.md`,
  `README.md`, and `src/README.md` before integration. Its core API includes
  `memory_lookup`, `memory_get`, `memory_rate`, `memory_save_trace`,
  `memory_trace_status`, and `memory_worker_status`.
- That memory project already implements hybrid retrieval, progressive disclosure,
  trace distillation, merge/feedback, and archival. Reuse it through an adapter;
  do not rebuild those systems here or modify its runtime data during inspection.
- Keep the projects independently runnable: the harness owns execution and
  verification; the memory server owns retrieval and the memory lifecycle.
  Configure the server location rather than hardcoding this sibling checkout.
  Do not import its implementation or copy its dependencies into the harness.

JEV was mentioned earlier but its intended meaning remains unconfirmed. It is
not a dependency or requirement. Do not let it block the agreed harness work.

## Architecture and boundaries

Suggested package layout (create as needed, not all at once):

```text
src/agent_harness/
  cli.py             command entry points
  config.py          validated configuration
  models/            provider interface, real and scripted adapters
  runtime/           loop, budgets, task lifecycle, delegation
  tools/             typed schemas and dispatch
  execution/         container lifecycle and workspace/artifact handling
  persistence/       SQLite checkpoints and action journal
  verification/      evaluator-controlled checks and result handling
  memory/            disabled and MCP-backed implementations
  evaluation/        repeatable experiment runner and reports
tests/
fixtures/
docs/
```

Keep three kinds of state separate:

- **Run state:** messages, task status, budget, pending actions, child results.
- **Workspace state:** source snapshot, patch, command output, artifacts.
- **Long-term memory:** selectively retrieved knowledge from previous tasks.

Suggested task lifecycle: `pending -> running -> succeeded | failed | cancelled`;
an interrupted process leaves work requiring reconciliation, not assumed success.
Give each run, task, tool call, and parent/child relationship stable IDs.

### Model and tool loop

Normalize tool calls and model responses through a small typed interface. Validate
arguments before dispatch. Bound turns, elapsed time, command duration, tool output,
and child count. Record token usage when available and label missing cost data.
Handle malformed calls, exceptions, cancellation, and exhausted budgets explicitly.

Initial tools: list/search/read files, apply an exact guarded edit, execute a
command, and finish with a structured result. Add delegation only in milestone 2.

### Delegation

The parent supplies a task, relevant context, allowed tools, and a budget. Children
have separate conversations and return structured findings, evidence paths, status,
and limitations. They do not receive the entire parent transcript by default.

First child: a read-only investigator with a snapshot workspace and no edit tools.
The parent is the sole patch writer. Disable recursive child spawning initially.
Aggregate child usage into the parent budget. A child timeout or failure is a
visible result that the parent can handle, never silent success.

Later parallel writers would require separate workspaces and explicit patch merge
handling; they are out of scope for the first implementation.

### Execution boundary

Use Docker as the first executor. Generated commands run inside containers with
only the task workspace mounted, an unprivileged user, resource/time limits,
network disabled by default, and no host credentials or Docker socket. Build a
fixture image with required dependencies ahead of execution. Treat Docker as a
practical local isolation mechanism, not a claim of protection against every
hostile-code attack. Bound process trees and output; clean up on cancellation.

Read-only investigator snapshots must actually be mounted read-only. Validate
paths and symlinks against workspace boundaries. Do not mount the original repo
or broad home directories. The host controls the model and container lifecycle.

### Verification and memory trust

The agent's final assertion is not the success criterion. The host launches a
separate verification process against the candidate patch with evaluator-owned
checks unavailable to the editing agent. Preserve baseline outcomes to distinguish
pre-existing failures. Verify expected behavior and regressions, not just whether
the agent's newly written test passes.

Retrieve memory before investigation; expand relevant L0 results to L1/L2 as
needed. Treat memory as fallible advice and validate it against current code.
Children return candidate observations; the parent owns ratings and trace writes.
Save observable evidence with verification status, including failures where useful;
do not label an entire remembered procedure correct merely because one test passed.
Memory outages should allow execution to continue with a visible degraded status.

## Milestones and acceptance criteria

Estimates are rough focused engineering time, excluding unpredictable provider or
Docker setup. Complete one milestone before enlarging scope.

### 1. Single-agent vertical slice — 8–12 hours

- Initialize `pyproject.toml`, `.python-version`, uv lockfile, package, and CLI.
- Implement one real provider adapter and one scripted adapter.
- Implement the bounded tool loop and Docker executor.
- Add two small bug fixtures and JSONL event/artifact output.
- Return a reviewable patch without changing the input repository.

Done when: a deterministic end-to-end run exercises real tools in Docker; timeout,
bad path, command failure, and cancellation behavior are tested; a separately
invoked real-model smoke run can be demonstrated when credentials are available.
Report environmental blockers rather than replacing Docker with host execution.

### 2. Explicit delegation — 6–10 hours

- Add `delegate` with isolated child context and typed results.
- Add one read-only investigator, capped concurrency, and shared budget accounting.
- Surface child lifecycle events and evidence in CLI output.

Done when: parent/child separation, child timeout, denied write, cancellation, and
budget exhaustion are checked; the same fixture runs with delegation on and off.

### 3. Independent verification — 5–8 hours

- Add host-controlled fixture checks and baseline/candidate comparison.
- Record verified success, verified failure, and inconclusive outcomes distinctly.
- Add a case where a superficial patch passes a visible test but fails a held-out
  check, and ensure the run is not reported as successful.

Done when: success depends on evaluator results and artifacts are reproducible.

### 4. Standalone MCP memory integration — 6–10 hours

- Add a disabled adapter and an MCP client adapter for `agentic-memory-mcp`.
  The Python package/module remains `agentic_memory`; the checkout folder name
  is not the import name.
- Support a configurable stdio server command, arguments, and environment. The
  local integration should launch `uv run --locked --directory <server-checkout>
  python -m agentic_memory.mcp_server.server` using the server's own environment.
- Use absolute paths for dedicated experiment storage via `MEMORY_DATA_DIR`,
  `CHROMA_PERSIST_DIR`, `TRACE_DIR`, and `TRACE_STATUS_DB_PATH`. Never use or reset
  the server checkout's existing default store. Keep this storage outside the
  agent execution sandbox and separate from evaluator-owned fixtures.
- Keep the MCP client/server connection on the host; execution containers receive
  neither the memory database nor server credentials. Bound startup and tool-call
  timeouts, close owned subprocesses, and surface connection errors.
- Support retrieval, progressive disclosure, feedback, trace submission, and
  reporting of pending/completed distillation without indefinite waiting.
- Separate useful, stale, incorrect, and irrelevant memory outcomes.
- Test adapter contracts with a stub MCP server, then run a separately documented
  integration against the standalone server and an isolated store. Label optional
  LLM distillation calls as API-consuming; do not equate a queued trace with a
  completed memory. Record the tested server revision for reproducibility.

Done when: memory-disabled execution works; unavailable memory fails gracefully;
one transfer task uses relevant memory and one stale-memory task rejects it;
the standalone server's existing data remains untouched. Migration of the server
is complete, but does not itself complete this harness integration milestone.

### 5. Checkpoint and recovery — 6–10 hours

- Persist task state and action intent/result with SQLite.
- Reconcile workspace/container state after an interruption.
- Retry safe reads; do not blindly replay an uncertain mutating command.
- For ambiguous writes, restore a known workspace snapshot or surface a recoverable
  interruption for user review. Do not claim exactly-once arbitrary shell execution.

Done when: forced interruption before/during/after a tool call has documented,
tested behavior without hidden duplicate mutations or false completion.

### 6. Evaluation and portfolio pack — 6–10 hours

Compare a 2x2 matrix: single/delegated agent x memory disabled/enabled. Fix model,
fixture revisions, environment, and comparable total budgets. Record actual cost
and time differences; more agents need not improve results.

Start with 8–12 tasks and repeated runs as budget allows. Include related-task
transfer, misleading memory, insufficient information, and failure recovery. Keep
development tasks separate from held-out tasks. Freeze memory snapshots for each
condition and prevent evaluation results leaking into later comparison runs.

Metrics: evaluator-verified success, regression rate, wall time, tool calls,
token/cost usage, stale-memory rejection, and recovery outcome. Record model
identifiers, prompt versions, memory snapshots, and seeds where supported. Show
raw counts and variability; do not infer broad performance from a tiny benchmark.

Deliver: README with uv setup, CLI demo, architecture/tradeoff notes, reproducible
evaluation commands, results table, and a short failure retrospective. Distinguish
scripted tests, live-model runs, synthetic fixtures, and actual user usage.

## Scope limits and stopping point

No web UI, SaaS deployment, generic plugin marketplace, recursive agent swarm,
fine-tuning, Kubernetes, or custom isolation runtime for v1. Start with one model
provider and one executor. No claim of self-improvement until measured comparison
supports it. No resume metrics before real results exist.

Reassess after milestone 3: if delegation adds complexity without benefit, retain
it as an evaluated optional mode. Stop v1 after a credible local demo and evaluation
report; use observed failures to choose further work.

## Suggested continuation prompt in a new Codex session

> Read AGENTS.md, PLAN.md, README.md, and docs/milestone-3.md. Continue with
> milestone 4 using uv. Connect to agentic-memory-mcp through a configurable MCP
> adapter and dedicated experiment storage. Preserve memory-disabled execution,
> evaluator-owned verification, and single/delegated modes. Test useful and stale
> memory cases, handle outages visibly, and leave sibling projects unchanged.
