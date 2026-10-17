"""CLI for reproducible scripted demonstrations and explicit paid smoke runs."""

import argparse
import asyncio
import json
import signal
import sys
import uuid
from pathlib import Path

from agent_harness.config import settings
from agent_harness.providers import (
    OpenAIProvider,
    ScriptedProvider,
    demo_actions,
    investigation_request,
    investigator_actions,
)
from agent_harness.runtime import run
from agent_harness.schema import Limits
from agent_harness.verification import PROFILES


def parser(config=None):
    config = settings() if config is None else config

    def value(name, default=None):
        return config.get("HARNESS_" + name) or default

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", type=Path, default=value("REPO"), required=not value("REPO"))
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--scripted", choices=["inclusive_sum", "slugify", "inclusive_sum_superficial"]
    )
    mode.add_argument("--live", action="store_true", help="Consumes paid OpenAI API calls")
    p.add_argument(
        "--delegate", action="store_true", help="Enable one read-only investigator at a time"
    )
    p.add_argument("--max-children", type=int, default=value("MAX_CHILDREN", 2))
    p.add_argument("--model", default=value("MODEL"), help="Model ID, or HARNESS_MODEL")
    p.add_argument(
        "--task",
        default=value("TASK", "Fix the bug exposed by the existing tests. Preserve tests."),
    )
    p.add_argument(
        "--output",
        type=Path,
        default=value("OUTPUT"),
        help="New directory outside the source repository",
    )
    p.add_argument("--image", default=value("IMAGE", "agent-harness-exec:local"))
    p.add_argument(
        "--evaluator",
        choices=sorted(PROFILES),
        help="Host-owned fixture checks; required for verified success",
    )
    p.add_argument("--check", default=value("CHECK", "python -m unittest discover -v"))
    p.add_argument("--max-turns", type=int, default=value("MAX_TURNS", 16))
    p.add_argument("--max-tools", type=int, default=value("MAX_TOOLS", 30))
    p.add_argument("--max-seconds", type=float, default=value("MAX_SECONDS", 180))
    p.add_argument("--command-seconds", type=float, default=value("COMMAND_SECONDS", 30))
    p.add_argument("--output-bytes", type=int, default=value("OUTPUT_BYTES", 16000))
    p.add_argument("--max-output-tokens", type=int, default=value("MAX_OUTPUT_TOKENS", 2048))
    return p


async def execute(args, limits):
    provider = (
        OpenAIProvider(args.model, limits.max_output_tokens, api_key=args.api_key)
        if args.live
        else ScriptedProvider(
            ([("delegate", investigation_request())] if args.delegate else [])
            + demo_actions(args.scripted)
        )
    )
    # OpenAIProvider is a stateless transport; each child owns its message list.
    # Scripted children require a fresh action iterator per invocation.
    child_factory = None
    if args.delegate:
        child_factory = (
            (lambda: provider)
            if args.live
            else (lambda: ScriptedProvider(investigator_actions(args.scripted)))
        )
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    interrupted = False

    def cancel_once():
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, cancel_once)

    def progress(kind, data):
        detail = data.get("name", data.get("phase", ""))
        if data.get("parent_task_id"):
            detail = f"child={data['task_id']} {detail}"
        if kind == "child_finished":
            child = data["result"]
            detail += f" status={child['status']} evidence={child['evidence_paths']}"
        if kind == "verification_finished":
            detail += f" status={data['result']['status']}"
        print(f"{kind} {detail}".rstrip(), file=sys.stderr, flush=True)

    try:
        return await run(
            args.repo,
            args.output or Path("runs") / uuid.uuid4().hex,
            args.task,
            provider,
            limits,
            args.image,
            args.check,
            progress=progress,
            child_provider_factory=child_factory,
            evaluator=args.evaluator,
        )
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
        if args.live:
            await provider.close()


def main():
    config = settings()
    p = parser(config)
    args = p.parse_args()
    args.api_key = config.get("OPENAI_API_KEY", "")
    if args.live and (not args.model or not args.api_key.strip()):
        p.error("--live requires a model and nonempty OPENAI_API_KEY in .env or the environment")
    if args.live:
        print("LIVE API RUN: consumes API tokens; dollar cost is not calculated.", file=sys.stderr)
    try:
        limits = Limits(
            turns=args.max_turns,
            children=args.max_children,
            tool_calls=args.max_tools,
            seconds=args.max_seconds,
            command_seconds=args.command_seconds,
            output_bytes=args.output_bytes,
            max_output_tokens=args.max_output_tokens,
        )
        result = asyncio.run(execute(args, limits))
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return {"succeeded": 0, "cancelled": 130, "inconclusive": 3}.get(result["status"], 1)
