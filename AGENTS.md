# Project instructions

Read `PLAN.md` before implementing. This is an independent learning and portfolio
project: a small coding-agent harness with explicit delegation, isolated execution,
verification, recovery, and integration with an existing agent-memory server.

- Use Python and uv for dependency management and commands. Commit `uv.lock` once
  the project is initialized. Use `uv run` and `uv sync --locked` after locking.
- Build the orchestration here. Do not adopt Deep Agents or another prebuilt agent
  orchestration runtime. A model SDK and the MCP client SDK are appropriate.
- Work in milestone order. Deliver working vertical slices before abstractions.
- Existing sibling repositories are read-only references, not dependencies to edit.
- The memory integration target is `../agentic-memory-mcp/`. Connect through MCP;
  keep its dependencies separate and use dedicated harness memory storage rather
  than its default `data/` directory. Memory integration belongs to milestone 4.
- Keep real model calls separate from deterministic tests. Label API-consuming
  commands and report actual usage; never describe scripted responses as live AI.
- Run generated code in the execution container, never directly on the host.
  Keep model credentials and the Docker socket out of execution containers.
- Store observable actions/results, not requests for hidden chain-of-thought.
- Do not automatically publish, deploy, push commits, or modify external repos.
- Report what was implemented, how it was verified, and remaining limitations.

Consult `README.md`, `docs/milestone-1.md`, and `docs/milestone-2.md` for the
implemented baseline and recorded validation. Milestones 1–2 are implemented;
milestones 3–6 remain planned.
When asked to continue implementation, start with the next unfinished milestone;
no new architecture approval is needed for routine choices consistent with the plan.
