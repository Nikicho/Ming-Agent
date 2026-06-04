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
    observations: list[dict[str, Any]] = field(default_factory=list)
    assessments: list[dict[str, Any]] = field(default_factory=list)
    final_output: str = ""

    def add_tool_event(self, event: ToolEvent) -> None:
        data = asdict(event)
        data.setdefault("event_id", f"evt-{len(self.tool_events) + 1}")
        self.tool_events.append(data)

    def add_observation(self, kind: str, summary: str) -> None:
        self.observations.append({"kind": kind, "summary": summary})

    def add_assessment(self, decision: str, reason: str) -> None:
        self.assessments.append({"decision": decision, "reason": reason})

    def save(self, root: str | Path | None = None) -> Path:
        trace_root = Path(root) if root else Path.cwd() / ".ming" / "traces"
        trace_root.mkdir(parents=True, exist_ok=True)
        path = trace_root / f"{self.turn_id}.json"
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    @staticmethod
    def expand_event(trace_path: str | Path, event_id: str) -> dict[str, Any] | None:
        payload = json.loads(Path(trace_path).read_text(encoding="utf-8"))
        for event in payload.get("tool_events", []):
            if event.get("event_id") == event_id:
                return event
        return None


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
            "trace_path": str(trace_path),
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
