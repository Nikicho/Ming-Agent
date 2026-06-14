"""Checkpoint persistence for dialog context recovery."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ming.core.llm import Message
from ming.core.todo import TodoState


def new_turn_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


class CheckpointStore:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root else Path.cwd() / ".ming" / "checkpoints"
        self.root.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        turn_id: str,
        messages: list[Message],
        notepad_path: Path,
        todo: TodoState | dict,
        changed_files: list[str] | None = None,
        name: str | None = None,
    ) -> Path:
        checkpoint_dir = self.root / turn_id
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        path = checkpoint_dir / "checkpoint.json"
        todo_payload = todo.to_dict() if hasattr(todo, "to_dict") else todo
        payload = {
            "turn_id": turn_id,
            "name": name or self._default_name(messages),
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "messages": [m.model_dump(exclude_none=True) for m in messages],
            "messages_summary": self._summarize_messages(messages),
            "notepad_path": str(notepad_path),
            "todo": todo_payload,
            "changed_files": changed_files or [],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def latest(self) -> Path | None:
        checkpoints = sorted(self.root.glob("*/checkpoint.json"), key=lambda p: p.stat().st_mtime)
        return checkpoints[-1] if checkpoints else None

    def resolve(self, checkpoint_id: str) -> Path | None:
        if checkpoint_id == "latest":
            return self.latest()
        candidate = self.root / checkpoint_id / "checkpoint.json"
        return candidate if candidate.exists() else None

    def load(self, path: str | Path) -> dict[str, Any]:
        checkpoint_path = Path(path)
        return json.loads(checkpoint_path.read_text(encoding="utf-8"))

    def cleanup(self, keep: int = 20) -> int:
        checkpoints = sorted(self.root.glob("*/checkpoint.json"), key=lambda p: p.stat().st_mtime)
        stale = checkpoints[: max(0, len(checkpoints) - keep)]
        removed = 0
        for checkpoint in stale:
            for child in checkpoint.parent.iterdir():
                child.unlink()
            checkpoint.parent.rmdir()
            removed += 1
        return removed

    def _summarize_messages(self, messages: list[Message], max_chars: int = 500) -> str:
        summary = "\n".join(f"{message.role}: {message.content[:120]}" for message in messages[-6:])
        return summary[:max_chars]

    def _default_name(self, messages: list[Message]) -> str:
        for message in reversed(messages):
            if message.role == "user" and message.content:
                return message.content[:80]
        return "checkpoint"
