"""Context management — four-layer model + compaction.

Layers (stable → dynamic, cache-friendly order):
  base_layer:    system prompt, T2 bias checklist, personality (cross-session stable)
  session_layer: user model, retrieved memories, project context (session stable)
  dialog_layer:  conversation history, tool results (grows per turn)
  instant_layer: current user input, gate/fork injections (per turn fresh)
"""

import logging
from pathlib import Path
from typing import Any

from ming.context.assembler import ContextAssembler, ContextAssemblyInput
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
        self.instant_layer: list[Message] = []   # current turn instructions
        self.dialog_history: list[Message] = []  # conversation turns
        self.turn_todo: str = ""
        self.turn_notepad_path: Path | None = None
        self.turn_tool_names: list[str] = []
        self.pinned_evidence: list[str] = []
        self.last_compaction_verified = False
        self.assembler = ContextAssembler()
        self._compaction_count = 0

    def set_base(self, system_prompt: str) -> None:
        """Set the base layer (system prompt). Called once at init."""
        self.base_layer = [Message(role="system", content=system_prompt)]

    def add_session_context(self, content: str, label: str = "context") -> None:
        """Add session-layer context (memories, project files)."""
        self.session_layer.append(
            Message(role="system", content=f"[{label}]\n{content}")
        )

    def replace_session_context(self, content: str, label: str = "context") -> int:
        """Replace session-layer context with the same label."""
        prefix = f"[{label}]"
        before = len(self.session_layer)
        self.session_layer = [
            message for message in self.session_layer if not message.content.startswith(prefix)
        ]
        removed = before - len(self.session_layer)
        if content:
            self.add_session_context(content, label=label)
        return removed

    def set_instant_context(self, content: str) -> None:
        """Set per-turn instant context."""
        self.instant_layer = [Message(role="system", content=content)] if content else []

    def clear_instant_context(self) -> int:
        """Clear current turn instant/workbench context."""
        removed = len(self.instant_layer)
        self.instant_layer = []
        self.turn_todo = ""
        self.turn_notepad_path = None
        self.turn_tool_names = []
        return removed

    def set_turn_workbench(
        self,
        *,
        todo: str = "",
        notepad_path: str | Path | None = None,
        tool_names: list[str] | None = None,
    ) -> None:
        """Set transient per-turn workbench context."""
        self.turn_todo = todo
        self.turn_notepad_path = Path(notepad_path) if notepad_path else None
        self.turn_tool_names = list(tool_names or [])

    def pin_evidence(self, evidence: str) -> None:
        """Pin compact evidence so pruning/summarization keeps it visible."""
        text = evidence.strip()
        if text and text not in self.pinned_evidence:
            self.pinned_evidence.append(text)

    def add_message(self, message: Message) -> None:
        """Add a message to dialog history."""
        self.dialog_history.append(message)

    def clear_dialog(self) -> int:
        """Clear dialog history while preserving base/session context."""
        removed = len(self.dialog_history)
        self.dialog_history = []
        return removed

    def clear_session_context(self) -> int:
        """Clear session-layer context for the current process only."""
        removed = len(self.session_layer)
        self.session_layer = []
        return removed

    def get_messages(self) -> list[Message]:
        """Assemble the full context through ContextAssembler."""
        return self.assembler.assemble(
            ContextAssemblyInput(
                base=self.base_layer,
                session=self.session_layer,
                dialog=self.dialog_history,
                instant="\n".join(m.content for m in self.instant_layer),
                todo=self.turn_todo,
                notepad_path=self.turn_notepad_path,
                tool_names=self.turn_tool_names,
                pinned_evidence=self.pinned_evidence,
            )
        )

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
        pinned_block = "\n".join(self.pinned_evidence)
        summary_prompt = (
            "Summarize the following conversation concisely. Preserve:\n"
            "- Key decisions made\n"
            "- Unresolved questions\n"
            "- Important file paths and code changes\n"
            "- User preferences expressed\n"
            "- All pinned evidence listed below\n\n"
            f"[pinned evidence]\n{pinned_block}\n\n"
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
        missing_evidence = [
            evidence for evidence in self.pinned_evidence if evidence not in summary_text
        ]
        self.last_compaction_verified = not missing_evidence
        if missing_evidence:
            summary_text = (
                "[Pinned evidence preserved after compaction]\n"
                + "\n".join(missing_evidence)
                + "\n\n"
                + summary_text
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
