"""LLM integration layer for OpenAI-compatible chat completion APIs."""

from typing import Any

import httpx
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
    """Call an OpenAI-compatible chat completions endpoint.

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
        api_base = _resolve_api_base(config.api_base, model)
        payload: dict[str, Any] = {
            "model": _provider_model_name(model),
            "messages": _serialize_messages(messages, model),
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
        }
        if tools:
            payload["tools"] = tools

        try:
            response = await _post_chat_completion(
                api_base=api_base,
                api_key=config.api_key,
                payload=payload,
                timeout_seconds=config.request_timeout_seconds,
            )
            break
        except Exception as exc:
            last_error = exc
    else:
        assert last_error is not None
        raise last_error

    choices = response.get("choices") or []
    if not choices:
        raise LLMProviderError("Provider response did not include choices.")
    choice = choices[0]
    message = choice.get("message") or {}

    # Extract tool calls if present
    tool_calls_data = None
    if message.get("tool_calls"):
        tool_calls_data = [
            {
                "id": tc.get("id", ""),
                "type": tc.get("type", "function"),
                "function": {
                    "name": (tc.get("function") or {}).get("name", ""),
                    "arguments": (tc.get("function") or {}).get("arguments", ""),
                },
            }
            for tc in message["tool_calls"]
        ]

    usage = response.get("usage") or {}
    return LLMResponse(
        content=message.get("content") or "",
        finish_reason=choice.get("finish_reason") or "stop",
        usage={
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
        },
        tool_calls=tool_calls_data,
    )


class LLMProviderError(RuntimeError):
    """Raised when a provider returns an invalid or unsuccessful response."""


async def _post_chat_completion(
    *,
    api_base: str,
    api_key: str,
    payload: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    if not api_key:
        raise LLMProviderError("LLM API key is not configured.")

    url = api_base.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    timeout = httpx.Timeout(timeout_seconds, connect=min(20, timeout_seconds))
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            return response.json()
    except httpx.TimeoutException as exc:
        raise TimeoutError(
            f"LLM provider timed out after {timeout_seconds} seconds while requesting {url}."
        ) from exc
    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:800]
        raise LLMProviderError(
            f"LLM provider returned HTTP {exc.response.status_code}: {body}"
        ) from exc
    except httpx.HTTPError as exc:
        raise LLMProviderError(f"LLM provider request failed: {exc}") from exc


def _resolve_api_base(api_base: str, model: str) -> str:
    if api_base:
        return api_base
    provider = model.split("/", 1)[0].lower() if "/" in model else ""
    defaults = {
        "deepseek": "https://api.deepseek.com/v1",
        "glm": "https://open.bigmodel.cn/api/paas/v4",
        "zhipu": "https://open.bigmodel.cn/api/paas/v4",
        "minimax": "https://api.minimax.chat/v1",
    }
    if provider in defaults:
        return defaults[provider]
    raise LLMProviderError(
        "LLM API base URL is not configured. Set llm.api_base for this model."
    )


def _provider_model_name(model: str) -> str:
    provider, sep, name = model.partition("/")
    if sep and provider.lower() in {"deepseek", "glm", "zhipu", "minimax"}:
        return name
    return model


def _serialize_messages(messages: list[Message], model: str) -> list[dict[str, Any]]:
    serialized = []
    for message in messages:
        payload = message.model_dump(exclude_none=True)
        serialized.append(payload)
    return serialized
