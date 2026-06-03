"""Context management — four-layer model + compaction.

Layers (stable → dynamic, cache-friendly order):
  base_layer:    system prompt, T2 bias checklist, personality (cross-session stable)
  session_layer: user model, retrieved memories, project context (session stable)
  dialog_layer:  conversation history, tool results (grows per turn)
  instant_layer: current user input, gate/fork injections (per turn fresh)
"""

import logging
from typing import Any

from ming.core.llm import Message

logger = logging.getLogger("ming")

# Rough chars-to-tokens ratio for Chinese+English mixed content
CHARS_PER_TOKEN = 2.5


def estimate_tokens(messages: list[Message]) -> int:
    """Rough token estimate from message content length."""
    total_chars = sum(len(m.content) for m in messages)
    return int(total_chars / CHARS_PER_TOKEN)


class ContextManager:
    """Manages the four-layer context assembly and compaction."""

    def __init__(
        self,
        max_context_tokens: int = 128000,
        compaction_threshold: float = 0.50,
        safety_threshold: float = 0.85,
    ):
        self.max_context_tokens = max_context_tokens
        self.compaction_threshold = compaction_threshold
        self.safety_threshold = safety_threshold

        # Layer contents
        self.base_layer: list[Message] = []      # system prompt (set once)
        self.session_layer: list[Message] = []   # memories, project context
        self.dialog_history: list[Message] = []  # conversation turns
        self._compaction_count = 0

    def set_base(self, system_prompt: str) -> None:
        """Set the base layer (system prompt). Called once at init."""
        self.base_layer = [Message(role="system", content=system_prompt)]

    def add_session_context(self, content: str, label: str = "context") -> None:
        """Add session-layer context (memories, project files)."""
        self.session_layer.append(
            Message(role="system", content=f"[{label}]\n{content}")
        )

    def add_message(self, message: Message) -> None:
        """Add a message to dialog history."""
        self.dialog_history.append(message)

    def get_messages(self) -> list[Message]:
        """Assemble the full context: base + session + dialog."""
        return self.base_layer + self.session_layer + self.dialog_history

    def current_tokens(self) -> int:
        """Estimate current total token usage."""
        return estimate_tokens(self.get_messages())

    def needs_compaction(self) -> bool:
        """Check if dialog layer has exceeded compaction threshold."""
        tokens = self.current_tokens()
        return tokens > self.max_context_tokens * self.compaction_threshold

    def needs_safety_compaction(self) -> bool:
        """Check if we're approaching hard limit."""
        tokens = self.current_tokens()
        return tokens > self.max_context_tokens * self.safety_threshold

    async def compact(self, llm_call: Any) -> None:
        """Compact dialog history using LLM summarization.

        Args:
            llm_call: async function(messages, config) -> LLMResponse
        """
        if len(self.dialog_history) < 6:
            return  # Too few messages to compact

        self._compaction_count += 1
        logger.info(f"Compaction #{self._compaction_count}: {len(self.dialog_history)} messages")

        # Phase 1: Tool pruning — replace old tool outputs with short placeholders
        pruned_count = 0
        for i, msg in enumerate(self.dialog_history):
            if msg.role == "tool" and len(msg.content) > 200 and i < len(self.dialog_history) - 20:
                self.dialog_history[i] = Message(
                    role=msg.role,
                    content="[tool output cleared to save context]",
                    tool_call_id=msg.tool_call_id,
                )
                pruned_count += 1

        if pruned_count > 0:
            logger.info(f"  Pruned {pruned_count} old tool outputs")

        # Phase 2: Summarize old dialog, keep recent messages
        protect_recent = min(20, len(self.dialog_history))
        old_messages = self.dialog_history[:-protect_recent]
        recent_messages = self.dialog_history[-protect_recent:]

        if len(old_messages) < 4:
            return  # Not enough old messages to summarize

        # Build summarization request
        summary_prompt = (
            "Summarize the following conversation concisely. Preserve:\n"
            "- Key decisions made\n"
            "- Unresolved questions\n"
            "- Important file paths and code changes\n"
            "- User preferences expressed\n\n"
            "Format as a structured summary under 500 words."
        )

        summary_messages = [
            Message(role="system", content=summary_prompt),
            Message(role="user", content="\n\n".join(
                f"[{m.role}]: {m.content[:500]}" for m in old_messages if m.content
            )),
        ]

        try:
            response = await llm_call(
                messages=summary_messages,
                config=llm_call.__self__ if hasattr(llm_call, '__self__') else None,
            )
            summary_text = response.content
        except Exception as e:
            logger.warning(f"Compaction LLM call failed: {e}, falling back to truncation")
            summary_text = "\n".join(
                f"[{m.role}]: {m.content[:100]}" for m in old_messages[-10:] if m.content
            )

        # Replace old messages with summary
        summary_msg = Message(
            role="system",
            content=(
                f"[Conversation summary (compacted from {len(old_messages)} messages)]\n"
                f"{summary_text}"
            ),
        )
        self.dialog_history = [summary_msg] + recent_messages
        logger.info(
            "  Compacted: %s old → 1 summary + %s recent",
            len(old_messages),
            len(recent_messages),
        )
