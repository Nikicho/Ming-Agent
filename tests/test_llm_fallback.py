import pytest

from ming.config import LLMConfig
from ming.core.llm import Message, call_llm


class FakeMessage:
    @staticmethod
    def payload(content="ok"):
        return {
            "choices": [
                {
                    "message": {"content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 2,
                "total_tokens": 3,
            },
        }


@pytest.mark.asyncio
async def test_call_llm_tries_fallback_model_after_primary_failure(monkeypatch):
    seen_models = []
    seen_timeouts = []

    async def fake_post_chat_completion(**kwargs):
        seen_models.append(kwargs["payload"]["model"])
        seen_timeouts.append(kwargs["timeout_seconds"])
        if len(seen_models) == 1:
            raise RuntimeError("primary down")
        return FakeMessage.payload()

    monkeypatch.setattr("ming.core.llm._post_chat_completion", fake_post_chat_completion)

    response = await call_llm(
        messages=[Message(role="user", content="hi")],
        config=LLMConfig(
            model="primary",
            fallback_models=["fallback"],
            api_key="test",
            api_base="https://example.test/v1",
        ),
    )

    assert response.content == "ok"
    assert seen_models == ["primary", "fallback"]
    assert seen_timeouts == [90, 90]


@pytest.mark.asyncio
async def test_call_llm_passes_configured_request_timeout(monkeypatch):
    seen_kwargs = {}

    async def fake_post_chat_completion(**kwargs):
        seen_kwargs.update(kwargs)
        return FakeMessage.payload()

    monkeypatch.setattr("ming.core.llm._post_chat_completion", fake_post_chat_completion)

    await call_llm(
        messages=[Message(role="user", content="hi")],
        config=LLMConfig(
            model="primary",
            api_key="test",
            api_base="https://example.test/v1",
            request_timeout_seconds=45,
        ),
    )

    assert seen_kwargs["timeout_seconds"] == 45


@pytest.mark.asyncio
async def test_call_llm_uses_provider_defaults_and_model_alias(monkeypatch):
    seen_kwargs = {}

    async def fake_post_chat_completion(**kwargs):
        seen_kwargs.update(kwargs)
        return FakeMessage.payload()

    monkeypatch.setattr("ming.core.llm._post_chat_completion", fake_post_chat_completion)

    await call_llm(
        messages=[
            Message(role="system", content="stable base"),
            Message(role="user", content="hi"),
        ],
        config=LLMConfig(model="deepseek/deepseek-chat", api_key="test"),
    )

    assert seen_kwargs["api_base"] == "https://api.deepseek.com/v1"
    assert seen_kwargs["payload"]["model"] == "deepseek-chat"
    assert seen_kwargs["payload"]["messages"][0]["role"] == "system"
