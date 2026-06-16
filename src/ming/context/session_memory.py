"""Session memory extraction for long-running Ming conversations."""

import logging
from pathlib import Path

from ming.config import LLMConfig
from ming.core.llm import Message, call_llm

logger = logging.getLogger("ming")

SESSION_MEMORY_INIT_TOKENS = 40_000
SESSION_MEMORY_UPDATE_INTERVAL = 20_000
SESSION_MEMORY_MAX_TOOL_CALLS = 5

EXTRACTION_PROMPT = """\
你是一个信息提取助手。从下面的对话中提取关键信息，包括：
- 用户做出的关键决策
- 发现的重要事实（文件路径、bug 原因、架构约束）
- 未解决的问题
- 用户明确表达的偏好或规则

输出格式：用 markdown 列表，每条一个关键信息。只保留对后续工作有用的内容，跳过闲聊。
上限 30 条。如果之前已有提取结果，做增量更新：保留仍有效的，新增新发现的，删除已过时的。
"""


class SessionMemory:
    """Extract and persist session-level key information."""

    def __init__(self, session_dir: Path, llm_config: LLMConfig):
        self.session_dir = session_dir
        self.llm_config = llm_config
        self.memory_path = session_dir / "__SESSION_MEMORY.md"
        self._last_extraction_tokens = 0
        self._tool_calls_since_update = 0
        self._current_memory = ""

        self.session_dir.mkdir(parents=True, exist_ok=True)
        if self.memory_path.exists():
            self._current_memory = self.memory_path.read_text(
                encoding="utf-8",
                errors="replace",
            )

    def record_tool_call(self) -> None:
        self._tool_calls_since_update += 1

    def should_extract(self, current_tokens: int) -> bool:
        if not self._current_memory and current_tokens >= SESSION_MEMORY_INIT_TOKENS:
            return True

        if self._current_memory:
            token_delta = current_tokens - self._last_extraction_tokens
            if token_delta >= SESSION_MEMORY_UPDATE_INTERVAL:
                return True
            if self._tool_calls_since_update >= SESSION_MEMORY_MAX_TOOL_CALLS:
                return True

        return False

    async def extract(self, dialog: list[Message], current_tokens: int) -> str | None:
        return await self.extract_with_llm(dialog, current_tokens, call_llm)

    async def extract_with_llm(
        self,
        dialog: list[Message],
        current_tokens: int,
        llm_call,
    ) -> str | None:
        if len(dialog) < 4:
            return None

        previous = (
            f"\n\n[之前的提取结果]\n{self._current_memory}"
            if self._current_memory
            else ""
        )
        transcript = "\n\n".join(
            f"[{message.role}]: {message.content[:300]}"
            for message in dialog[-50:]
            if message.content and message.role != "system"
        )
        if not transcript:
            return None

        extract_messages = [
            Message(role="system", content=EXTRACTION_PROMPT + previous),
            Message(role="user", content=transcript),
        ]

        try:
            response = await llm_call(messages=extract_messages, config=self.llm_config)
        except Exception as exc:
            logger.warning("Session memory extraction failed: %s", exc)
            return None

        memory_text = response.content.strip()
        if not memory_text:
            return None

        self._current_memory = memory_text
        self._last_extraction_tokens = current_tokens
        self._tool_calls_since_update = 0
        self.memory_path.write_text(memory_text, encoding="utf-8")
        logger.info("Session memory updated: %s", self.memory_path)
        return memory_text

    def get_context_block(self) -> str:
        return self._current_memory
