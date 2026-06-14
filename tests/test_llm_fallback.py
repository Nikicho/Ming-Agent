import pytest

from ming.config import LLMConfig
from ming.core.llm import Message, call_llm


class FakeMessage:
    content = "ok"
    tool_calls = None


class FakeChoice:
    message = FakeMessage()
    finish_reason = "stop"


class FakeUsage:
    prompt_tokens = 1
    completion_tokens = 2
    total_tokens = 3


class FakeResponse:
    choices = [FakeChoice()]
    usage = FakeUsage()


@pytest.mark.asyncio
async def test_call_llm_tries_fallback_model_after_primary_failure(monkeypatch):
    seen_models = []
    seen_timeouts = []

    async def fake_completion(**kwargs):
        seen_models.append(kwargs["model"])
        seen_timeouts.append(kwargs["timeout"])
        if len(seen_models) == 1:
            raise RuntimeError("primary down")
        return FakeResponse()

    monkeypatch.setattr("litellm.acompletion", fake_completion)

    response = await call_llm(
        messages=[Message(role="user", content="hi")],
        config=LLMConfig(model="primary", fallback_models=["fallback"], api_key="test"),
    )

    assert response.content == "ok"
    assert seen_models == ["primary", "fallback"]
    assert seen_timeouts == [90, 90]


@pytest.mark.asyncio
async def test_call_llm_passes_configured_request_timeout(monkeypatch):
    seen_kwargs = {}

    async def fake_completion(**kwargs):
        seen_kwargs.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr("litellm.acompletion", fake_completion)

    await call_llm(
        messages=[Message(role="user", content="hi")],
        config=LLMConfig(model="primary", api_key="test", request_timeout_seconds=45),
    )

    assert seen_kwargs["timeout"] == 45
