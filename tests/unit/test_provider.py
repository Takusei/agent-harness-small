from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from openai import AsyncOpenAI

from agent_harness.providers import OpenAIProvider
from agent_harness.schema import tool_schemas


async def test_real_sdk_request_and_response_without_network():
    requests = []

    def respond(request):
        import json

        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "resp_test",
                "object": "response",
                "created_at": 0,
                "status": "completed",
                "model": "test-model",
                "output": [
                    {
                        "type": "function_call",
                        "id": "fc_test",
                        "call_id": "call_test",
                        "name": "list_files",
                        "arguments": '{"query":""}',
                        "status": "completed",
                    }
                ],
                "usage": {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30},
                "parallel_tool_calls": False,
                "tool_choice": "auto",
                "tools": [],
            },
        )

    client = AsyncOpenAI(
        api_key="test-not-a-real-key",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    provider = OpenAIProvider("test-model", 256, client)
    turn = await provider.respond([{"role": "user", "content": "test"}], tool_schemas(), 2)
    await provider.close()
    assert turn.calls[0].name == "list_files"
    assert turn.usage["total_tokens"] == 30
    assert requests[0]["parallel_tool_calls"] is False
    assert requests[0]["store"] is False
    for tool in requests[0]["tools"]:
        assert tool["strict"] is True
        assert tool["parameters"]["additionalProperties"] is False
        assert set(tool["parameters"]["required"]) == set(tool["parameters"]["properties"])


async def test_missing_usage_stays_unknown():
    client = SimpleNamespace(
        responses=SimpleNamespace(
            create=AsyncMock(return_value=SimpleNamespace(output=[], output_text="", usage=None))
        )
    )
    turn = await OpenAIProvider("test", 256, client).respond([], [], 2)
    assert turn.usage is None
