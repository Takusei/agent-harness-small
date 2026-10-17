# Milestone 2: explicit delegation

Recorded 2026-10-17. This milestone adds an optional read-only investigator to
both provider modes. Validation below is deterministic; no live API calls were
made for this milestone.

## Implemented

- `--delegate` enables the parent tool; without it the single-agent path remains.
- The parent supplies a task, selected context, file-tool allowlist, and child
  turn/tool/time budgets. Each child owns a separate conversation and a snapshot
  copied from the candidate workspace at delegation time.
- Docker mounts the child snapshot read-only. Dispatch permits only the selected
  list/search/read tools plus structured `finish`. Edits, commands, and recursive
  delegation are rejected even if emitted by a provider.
- One child runs at a time, with a default maximum of two children per run.
  Parent and child calls share run-level turn/tool/time budgets. Usage is totaled
  once, with child subtotals and incomplete-usage flags for interrupted requests.
- Typed child results retain findings, evidence paths, status, limitations,
  errors, usage, artifact locations, and cleanup results. Paths must identify
  snapshot files; correctness of the findings is not independently verified.
- Journal events link run, parent, child, and delegation call IDs. CLI progress
  shows child lifecycle, terminal status, and evidence paths.
- Local child failures return to the parent; parent cancellation propagates to
  the child. Container cleanup errors prevent run success.

## Verification

Before changes, `uv run pytest --run-docker -q` passed 29 tests.
After changes, the full suite passed **51 tests in 18.26 seconds**: 39 unit tests
and 12 real Docker integration cases. Locked sync, Ruff lint/format checks, and
`git diff --check` also passed. The checks use the existing locked environment:

```sh
uv sync --locked
uv run pytest --run-docker -q
uv run python -m ruff check .
uv run python -m ruff format --check .
git diff --check
```

Acceptance coverage includes:

| Boundary | Validation |
| --- | --- |
| Separate context | Child sees only its system prompt and supplied task/context, not parent transcript or baseline output |
| Separate snapshots | Parent edits do not change the copied child snapshot; source remains unchanged |
| Tool authority | Write, command, recursive delegation, and omitted tools are rejected before executor dispatch |
| Docker mount | Direct shell and file-edit attempts against the child mount fail with unchanged content |
| Shared budgets | Parent and child calls never exceed run call limits; usage includes both |
| Local budgets | Child exhaustion and timeout return failed results that the parent can handle |
| Child count/concurrency | Maximum count enforced; lifecycle events alternate start/finish with fresh providers |
| Cancellation | Cancelling an active child removes its Docker container and retains parent/child results |
| Timeout cleanup | Timing out an active child removes its Docker container; parent can continue |
| Regression | Both bug fixtures run with delegation on and off, fixing visible tests without changing sources |

The initial active-container tests waited for a marker in a buffered stdout
artifact and failed to detect startup. The test now queries Docker's running
state before cancelling or awaiting timeout. Both targeted cases passed after
this correction.

## Retained CLI demonstration

```sh
uv run python -m agent_harness --repo fixtures/inclusive_sum \
  --scripted inclusive_sum --delegate --output runs/milestone-2-delegated
```

The retained run has ID `db9040d6e72e4e229b35000239ae15af`. It completed in 1.773
seconds, with 10 scripted provider turns and 10 tool calls, including 3 child
turns/tools. It consumed **zero API calls and tokens**. Baseline: two failing
visible tests; candidate: all three visible tests passed. No cleanup errors.
The result, patch, journal, child snapshot, and artifacts are gitignored under
`runs/milestone-2-delegated/`.

## Limits and next milestone

The scripted parent follows a predetermined patch sequence; the demo does not
prove a model can use child findings or that delegation improves repair quality.
Live delegation is wired through the existing provider but was not exercised
against a real model. Its transport remains host-side and shares no credentials
with containers. Callers of the runtime own the provider factory's transports;
the CLI reuses and closes its single stateless OpenAI transport.

Concurrency is fixed at one; there are no parallel writers or recursive children.
Per-response output tokens are capped, but there is no total-token/dollar budget.
Child snapshots are read-only inside Docker, not immutable against a human on the
host. SIGKILL/crash recovery remains milestone 5. Snapshot copying and artifact
writes are small bounded synchronous operations; cleanup may extend wall time.

Visible tests remain writable by the parent, so successful runs retain
`verification: not_independently_verified`. Next is milestone 3: evaluator-owned
checks and explicit verified success/failure/inconclusive outcomes. Memory stays
in milestone 4; sibling repositories and memory stores were not modified.
