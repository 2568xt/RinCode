"""Regression tests for DeepSeek thinking-mode history replay."""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any

import pytest

from rincode.providers.litellm_provider import LiteLLMProvider


async def _successful_stream():
    yield SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content="ok", tool_calls=None),
                finish_reason="stop",
            )
        ]
    )


def _successful_response() -> SimpleNamespace:
    return SimpleNamespace(
        model="deepseek/deepseek-v4-flash",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="ok",
                    tool_calls=[],
                    reasoning_content=None,
                    thinking_blocks=None,
                ),
                finish_reason="stop",
            )
        ],
        usage=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True], ids=["chat", "chat_stream"])
async def test_deepseek_transport_replays_reasoning_for_every_assistant(
    monkeypatch: pytest.MonkeyPatch,
    stream: bool,
) -> None:
    """A tools request supplies the field for a prior plain assistant as well as preserving it."""
    messages = [
        {"role": "user", "content": "finish the first request"},
        {"role": "assistant", "content": "the first request is complete"},
        {"role": "user", "content": "continue with the next request"},
        {
            "role": "assistant",
            "content": "a response with recorded reasoning",
            "reasoning_content": "keep this reasoning",
        },
        {"role": "user", "content": "one more request"},
    ]
    original_messages = copy.deepcopy(messages)
    tools = [{"type": "function", "function": {"name": "read_file", "parameters": {}}}]
    captured: dict[str, Any] = {}

    async def fake_acompletion(**kwargs: Any):
        captured.update(kwargs)
        assert kwargs["tools"] == tools
        # Simulated DeepSeek transport validation: every historical assistant
        # message must carry the replay field whenever tools are present.
        assert all(
            "reasoning_content" in message
            for message in kwargs["messages"]
            if message.get("role") == "assistant"
        )
        return _successful_stream() if kwargs.get("stream") else _successful_response()

    monkeypatch.setattr("rincode.providers.litellm_provider.acompletion", fake_acompletion)
    provider = LiteLLMProvider(api_key="test-key", default_model="deepseek/deepseek-v4-flash")

    if stream:
        deltas = [delta async for delta in provider.chat_stream(messages=messages, tools=tools)]
        assert [delta.content for delta in deltas] == ["ok"]
    else:
        response = await provider.chat(messages=messages, tools=tools)
        assert response.finish_reason == "stop"

    payload_messages = captured["messages"]
    assert payload_messages[1]["reasoning_content"] == ""
    assert payload_messages[3]["reasoning_content"] == "keep this reasoning"
    assert messages == original_messages



@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("with_tools", [False, True])
@pytest.mark.parametrize("model", ["deepseek/deepseek-v4-flash", "openai/gpt-4o"])
async def test_reasoning_only_history_and_effort_reach_transport(monkeypatch, stream, with_tools, model):
    messages = [
        {"role": "user", "content": "repair"},
        {"role": "assistant", "content": "", "reasoning_content": "synthetic test value"},
        {"role": "user", "content": "continue"},
    ]
    original = copy.deepcopy(messages)
    captured = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return _successful_stream() if kwargs.get("stream") else _successful_response()

    monkeypatch.setattr("rincode.providers.litellm_provider.acompletion", fake_acompletion)
    extra = {"thinking": {"type": "enabled"}}
    provider = LiteLLMProvider(api_key="test-key", default_model=model, extra_body=extra)
    kwargs = dict(messages=messages, tools=[{"type": "function", "function": {"name": "read_file", "parameters": {}}}] if with_tools else None, reasoning_effort="low")
    if stream:
        assert [d.content async for d in provider.chat_stream(**kwargs)] == ["ok"]
    else:
        assert (await provider.chat(**kwargs)).finish_reason == "stop"
    expected_count = 2 if model.startswith("deepseek/") and not with_tools else 3
    assert len(captured["messages"]) == expected_count
    if model.startswith("deepseek/"):
        assert captured["extra_body"]["reasoning_effort"] == "low"
        assert "reasoning_effort" not in captured
    else:
        assert captured["reasoning_effort"] == "low"
        assert "reasoning_effort" not in captured["extra_body"]
    assert captured["extra_body"]["thinking"] == {"type": "enabled"}
    assert extra == {"thinking": {"type": "enabled"}}
    assert messages == original


def test_no_tools_filter_preserves_assistant_tool_pair():
    history = [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "long-test-call-id", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "long-test-call-id", "content": "source"},
    ]
    original = copy.deepcopy(history)
    result = LiteLLMProvider._sanitize_messages(history, require_reasoning_content_replay=True, drop_reasoning_only_assistants=True)
    assert len(result) == 2
    assert result[0]["tool_calls"][0]["id"] == result[1]["tool_call_id"]
    assert history == original
