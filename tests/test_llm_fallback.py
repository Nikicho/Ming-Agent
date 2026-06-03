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

    async def fake_completion(**kwargs):
        seen_models.append(kwargs["model"])
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
