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
        return messages

    def _read_notepad_summary(self, path: Path | None) -> str:
        if path is None or not path.exists():
            return ""
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) <= 2000:
            return text.strip()
        return text[:2000].rstrip() + "\n... (notepad truncated)"
