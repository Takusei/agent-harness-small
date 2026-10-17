"""Transport only: both providers return the same typed turn."""

import json
from dataclasses import dataclass, field
from typing import Protocol

from openai import AsyncOpenAI


@dataclass
class Call:
    id: str
    name: str
    arguments: str


@dataclass
class Turn:
    calls: list[Call] = field(default_factory=list)
    text: str = ""
    usage: dict[str, int] | None = None
    # Opaque API continuation items stay in memory, never in the observable journal.
    continuation: list[dict] = field(default_factory=list)


class Provider(Protocol):
    label: str

    async def respond(self, messages: list[dict], tools: list[dict], timeout: float) -> Turn: ...


class ScriptedProvider:
    label = "scripted (no AI, no API calls)"

    def __init__(self, actions: list[tuple[str, dict]]):
        self.actions = iter(actions)

    async def respond(self, messages, tools, timeout):
        try:
            name, arguments = next(self.actions)
        except StopIteration as exc:
            raise RuntimeError("Script exhausted without finish") from exc
        return Turn(
            [Call(f"script-{len(messages)}", name, json.dumps(arguments))],
            usage={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        )


class OpenAIProvider:
    def __init__(self, model: str, max_output_tokens: int, client=None, *, api_key=None):
        self.model = model
        self.label = f"openai:{model} (live API)"
        self.max_output_tokens = max_output_tokens
        self.client = client or AsyncOpenAI(max_retries=0, api_key=api_key)

    async def respond(self, messages, tools, timeout):
        response = await self.client.responses.create(
            model=self.model,
            input=messages,
            tools=tools,
            tool_choice="required",
            parallel_tool_calls=False,
            max_output_tokens=self.max_output_tokens,
            store=False,
            include=["reasoning.encrypted_content"],
            timeout=timeout,
        )
        usage = None
        if response.usage is not None:
            usage = {
                key: getattr(response.usage, key)
                for key in ("input_tokens", "output_tokens", "total_tokens")
            }
        return Turn(
            calls=[
                Call(item.call_id, item.name, item.arguments)
                for item in response.output
                if item.type == "function_call"
            ],
            text=response.output_text,
            usage=usage,
            continuation=[item.model_dump(exclude_none=True) for item in response.output],
        )

    async def close(self):
        await self.client.close()


def demo_actions(fixture: str) -> list[tuple[str, dict]]:
    superficial = fixture == "inclusive_sum_superficial"
    fixture = "inclusive_sum" if superficial else fixture
    edits = {
        "inclusive_sum": ("math_utils.py", "range(n)", "range(n + 1)"),
        "slugify": ("text_utils.py", 'text.strip().split(" ")', "text.strip().split()"),
    }
    path, old, new = edits[fixture]
    if superficial:
        old, new = "sum(range(n))", "{0: 0, 1: 1, 3: 6}.get(n, 0)"
    return [
        ("list_files", {"query": ""}),
        ("search_files", {"query": "def "}),
        ("read_file", {"path": path, "start": 1, "count": 200}),
        ("edit_file", {"path": path, "old": old, "new": new}),
        ("run_command", {"command": "python -m unittest discover -v"}),
        (
            "finish",
            {
                "summary": f"Applied scripted {fixture} repair.",
                "limitations": ["Fixed demonstration script; no model reasoning."],
            },
        ),
    ]


def investigation_request():
    return {
        "task": "Inspect the fixture and identify the bug; do not edit.",
        "context": "Look for boundary or whitespace handling errors.",
        "allowed_tools": ["list_files", "search_files", "read_file"],
        "turns": 4,
        "tool_calls": 4,
        "seconds": 30.0,
    }


def investigator_actions(fixture):
    if fixture == "inclusive_sum_superficial":
        fixture = "inclusive_sum"
    path = "math_utils.py" if fixture == "inclusive_sum" else "text_utils.py"
    finding = (
        "range(n) excludes the upper bound; use range(n + 1)."
        if fixture == "inclusive_sum"
        else "Splitting on a literal space retains empty fields; use split()."
    )
    return [
        ("list_files", {"query": ""}),
        ("read_file", {"path": path, "start": 1, "count": 200}),
        (
            "finish",
            {
                "findings": finding,
                "evidence_paths": [path],
                "limitations": ["Predetermined scripted findings, not live AI."],
            },
        ),
    ]
