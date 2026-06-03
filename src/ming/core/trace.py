"""Run trace and checkpoint persistence."""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ming.core.llm import Message
from ming.core.progress import ToolEvent
from ming.core.todo import TodoState


def new_turn_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


@dataclass
class RunTrace:
    turn_id: str
    user_input: str
    started_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    tool_events: list[dict[str, Any]] = field(default_factory=list)
    final_output: str = ""

    def add_tool_event(self, event: ToolEvent) -> None:
        self.tool_events.append(asdict(event))

    def save(self, root: str | Path | None = None) -> Path:
        trace_root = Path(root) if root else Path.cwd() / ".ming" / "traces"
        trace_root.mkdir(parents=True, exist_ok=True)
        path = trace_root / f"{self.turn_id}.json"
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        return path


class CheckpointStore:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root else Path.cwd() / ".ming" / "checkpoints"
        self.root.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        turn_id: str,
        messages: list[Message],
        trace_path: Path,
        notepad_path: Path,
        todo: TodoState,
    ) -> Path:
        checkpoint_dir = self.root / turn_id
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        path = checkpoint_dir / "checkpoint.json"
        payload = {
            "turn_id": turn_id,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "messages": [m.model_dump(exclude_none=True) for m in messages],
            "trace_path": str(trace_path),
            "notepad_path": str(notepad_path),
            "todo": todo.to_dict(),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def latest(self) -> Path | None:
        checkpoints = sorted(self.root.glob("*/checkpoint.json"), key=lambda p: p.stat().st_mtime)
        return checkpoints[-1] if checkpoints else None
