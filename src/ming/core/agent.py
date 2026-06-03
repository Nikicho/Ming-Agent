"""Core agent loop (α_LOOP) - P0 minimal version.

This is the simplest possible agent: read user input, call LLM, return response.
Tool calling and agentic loop will be added in P1.
"""

import asyncio

from ming.config import MingConfig, load_config
from ming.core.llm import LLMResponse, Message, call_llm

# P0: Minimal system prompt (will become the 基座层 in P2)
SYSTEM_PROMPT = """你是 Ming（明），一个增强人类 System 2 思维的 AI 助手。

你的名字来自《道德经》：「知常曰明，不知常，妄作凶」。
你的核心价值是帮助人类看清模式、避免妄作。

当前是 P0 阶段——你只能对话，还没有工具调用能力。请直接回答用户的问题。"""


class Agent:
    """Ming agent - P0 minimal implementation."""

    def __init__(self, config: MingConfig | None = None):
        self.config = config or load_config()
        self.messages: list[Message] = [
            Message(role="system", content=SYSTEM_PROMPT)
        ]

    async def chat(self, user_input: str) -> str:
        """Process a single user message and return the assistant response."""
        # Add user message
        self.messages.append(Message(role="user", content=user_input))

        # Call LLM
        response: LLMResponse = await call_llm(
            messages=self.messages,
            config=self.config.llm,
        )

        # Add assistant response to history
        self.messages.append(Message(role="assistant", content=response.content))

        return response.content

    def chat_sync(self, user_input: str) -> str:
        """Synchronous wrapper for chat()."""
        return asyncio.run(self.chat(user_input))
