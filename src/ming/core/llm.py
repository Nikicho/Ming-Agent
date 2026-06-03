"""LLM integration layer via LiteLLM.

Provides a unified interface for calling any LLM provider.
"""

from typing import Any

import litellm
from pydantic import BaseModel, Field

from ming.config import LLMConfig


class Message(BaseModel):
    """A single message in the conversation."""

    role: str  # "system", "user", "assistant", "tool"
    content: str
    # For tool calls/results (P1 onwards)
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    name: str | None = None


class LLMResponse(BaseModel):
    """Response from an LLM call."""

    content: str
    finish_reason: str  # "stop", "tool_calls", "length"
    # prompt_tokens, completion_tokens, total_tokens
    usage: dict[str, int] = Field(default_factory=dict)
    tool_calls: list[dict[str, Any]] | None = None


async def call_llm(
    messages: list[Message],
    config: LLMConfig,
    tools: list[dict[str, Any]] | None = None,
) -> LLMResponse:
    """Call the LLM via LiteLLM.

    Args:
        messages: Conversation history.
        config: LLM configuration.
        tools: Optional tool definitions (OpenAI function calling format).

    Returns:
        LLMResponse with content and metadata.
    """
    models = [config.model, *config.fallback_models]
    last_error: Exception | None = None

    for model in models:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [m.model_dump(exclude_none=True) for m in messages],
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
        }

        if config.api_key:
            kwargs["api_key"] = config.api_key
        if config.api_base:
            kwargs["api_base"] = config.api_base
        if tools:
            kwargs["tools"] = tools

        try:
            response = await litellm.acompletion(**kwargs)
            break
        except Exception as exc:
            last_error = exc
    else:
        assert last_error is not None
        raise last_error

    # Parse response
    choice = response.choices[0]
    message = choice.message

    # Extract tool calls if present
    tool_calls_data = None
    if message.tool_calls:
        tool_calls_data = [
            {
                "id": tc.id,
                "type": tc.type,
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in message.tool_calls
        ]

    return LLMResponse(
        content=message.content or "",
        finish_reason=choice.finish_reason or "stop",
        usage={
            "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
            "completion_tokens": response.usage.completion_tokens if response.usage else 0,
            "total_tokens": response.usage.total_tokens if response.usage else 0,
        },
        tool_calls=tool_calls_data,
    )
