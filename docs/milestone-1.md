# Milestone 1 validation record

Recorded 2026-10-16. This is a small vertical-slice demonstration, not a benchmark
of general coding ability.

## Implemented

- Python 3.12 CLI, uv lockfile, OpenAI Responses adapter and scripted adapter.
- Explicit bounded tool loop with strict schemas and structured finish results.
- Docker-only generated commands/file tools, guarded edits, bounded output,
  process-tree cleanup, and isolated disposable source copies.
- Two deliberately buggy fixtures, JSONL events, command artifacts and text patches.
- No delegation, independent evaluator, memory or checkpoint recovery yet.

## Deterministic validation

`uv sync --locked` succeeded. `uv run pytest --run-docker -q` passed all 25
tests: 18 unit tests and 7 Docker integration cases. Ruff lint and formatting
checks are part of the final validation commands in the README.

The Docker tests build the execution image and cover:

| Case | Observed result |
| --- | --- |
| Inclusive sum repair | Baseline fails; final three visible tests pass; original unchanged |
| Whitespace slug repair | Baseline fails; final three visible tests pass; original unchanged |
| Nonzero command | Exit 7 and stderr preserved |
| Output limits | Preview truncation and 1 MB artifact cap enforced |
| File guards | Absolute/traversal/symlink paths and stale/ambiguous edits rejected |
| Timeout | Container and descendant writer killed; no delayed workspace write |
| Cancellation | Named active container removed |
| Isolation | Non-root, no key/socket, no network, read-only root filesystem |

The unit suite covers strict/malformed tool calls, budgets, cancellation, failed
checks, provider exceptions, source/output separation, snapshot restrictions,
patch application, and actual SDK serialization against a mocked HTTP transport.
No unit test executes generated code on the host or makes a live API request.

A retained CLI demo is in `runs/milestone-1-inclusive-sum/` (gitignored), with
baseline/final artifacts and `patch.diff`. Its six scripted turns consumed zero
API calls and tokens. Integration tests also exercise the second fixture.

## Separately invoked live smoke test

Command equivalent:

```sh
uv run agent-harness --repo fixtures/inclusive_sum --live --model gpt-5-mini \
  --max-turns 10 --max-tools 10 --max-seconds 120 \
  --output runs/milestone-1-live
```

| Measurement | Result |
| --- | --- |
| Run ID | `34fec6fb467148199f4bf9a1685e5db8` |
| Provider/model requested | OpenAI / `gpt-5-mini` |
| Model calls / tool calls | 7 / 7 |
| Input tokens | 10,084 |
| Output tokens | 907 |
| Total tokens | 10,991 |
| Usage complete | Yes |
| Dollar cost | Unknown; not calculated |
| Wall time | 16.811 seconds |
| Baseline | 2 failures out of 3 visible tests |
| Final | 3 visible tests pass |
| Patch | `range(n)` changed to `range(n + 1)` in `math_utils.py` |
| Cleanup errors | None |

Raw result, JSONL trace, patch and command artifacts are retained under
`runs/milestone-1-live/`. They are not committed. The source fixture and its tests
remain unchanged. The run's verification field is `not_independently_verified`.

The model's final limitations text incorrectly says inputs below -1 produce
negative sums. In fact, `range(n + 1)` is empty for those inputs and its sum is
zero. The raw response is preserved as an observation, not endorsed as fact.
This illustrates why model assertions cannot serve as verification evidence.

## Failure found and fixed

The initial output-limit test exposed a subprocess cleanup hang: after a reader
stopped at the output cap, waiting for the Docker CLI could block on an undrained
pipe even after termination. Cleanup now stops the container, kills the client
when needed, cancels collectors, and drains the remaining pipes with a deadline.
The output-limit test and full Docker suite passed after the fix.

Docker was initially unavailable from direct shell probes, but became accessible
through the permitted test runner. All reported integration results above are
from actual containers; no host-execution fallback was introduced.

## Remaining limitations

Visible tests remain writable by the agent. There are no hidden/evaluator-owned
checks, persistent checkpoints, memory integration, or child agents. Only small
UTF-8 text repositories are supported; the Docker base tag is not digest-pinned,
workspace disk use has no hard quota, and hard host crashes can leave work requiring
manual cleanup. These limits are described further in the README and PLAN.md.

`uv.lock` is generated and present. Creating `.git` with `git init` was denied by
the workspace permission policy, so the requested local lockfile commit could not
be made in this session. No remote was configured or changed.
