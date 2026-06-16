"""Explicit context assembly for Ming's context workbench."""

from dataclasses import dataclass, field
from pathlib import Path

from ming.core.llm import Message


@dataclass
class ContextAssemblyInput:
    base: list[Message] = field(default_factory=list)
    session: list[Message] = field(default_factory=list)
    dialog: list[Message] = field(default_factory=list)
    instant: str = ""
    todo: str = ""
    notepad_path: Path | None = None
    tool_names: list[str] = field(default_factory=list)
    pinned_evidence: list[str] = field(default_factory=list)


class ContextAssembler:
    """Assemble stable and per-turn context in cache-friendly order."""

    def assemble(self, data: ContextAssemblyInput) -> list[Message]:
        messages = [*data.base, *data.session]

        if data.instant:
            messages.append(Message(role="system", content=f"[instant]\n{data.instant}"))
        if data.todo:
            messages.append(Message(role="system", content=f"[todo]\n{data.todo}"))

        notepad_summary = self._read_notepad_summary(data.notepad_path)
        if notepad_summary:
            messages.append(Message(role="system", content=f"[notepad]\n{notepad_summary}"))

        if data.pinned_evidence:
            messages.append(
                Message(
                    role="system",
                    content="[pinned evidence]\n" + "\n".join(data.pinned_evidence),
                )
            )

        if data.tool_names:
            messages.append(
                Message(
                    role="system",
                    content="[toolset]\n" + ", ".join(data.tool_names),
                )
            )

        messages.extend(data.dialog)
        return self._normalize_tool_pairs(messages)

    def _normalize_tool_pairs(self, messages: list[Message]) -> list[Message]:
        """Ensure tool calls and tool results stay paired before provider calls."""
        tool_use_ids: set[str] = set()
        for message in messages:
            for tool_call in message.tool_calls or []:
                call_id = tool_call.get("id")
                if call_id:
                    tool_use_ids.add(str(call_id))

        cleaned: list[Message] = []
        for message in messages:
            if (
                message.role == "tool"
                and message.tool_call_id
                and message.tool_call_id not in tool_use_ids
            ):
                continue
            cleaned.append(message)

        result_ids = {
            message.tool_call_id
            for message in cleaned
            if message.role == "tool" and message.tool_call_id
        }
        final: list[Message] = []
        for message in cleaned:
            if message.tool_calls:
                paired_calls = [
                    tool_call
                    for tool_call in message.tool_calls
                    if str(tool_call.get("id") or "") in result_ids
                ]
                if paired_calls:
                    message = message.model_copy(update={"tool_calls": paired_calls})
                elif message.content:
                    message = message.model_copy(update={"tool_calls": None})
                else:
                    continue
            final.append(message)
        return final

    def _read_notepad_summary(self, path: Path | None) -> str:
        if path is None or not path.exists():
            return ""
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) <= 2000:
            return text.strip()
        return text[:2000].rstrip() + "\n... (notepad truncated)"
