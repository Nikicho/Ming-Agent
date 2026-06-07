"""Dreaming pass for offline review and memory consolidation suggestions."""

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ming.memory.store import MemoryEntry, MemoryStore


@dataclass(frozen=True)
class DreamReport:
    mode: str
    generated_at: str
    summary: dict[str, Any]
    recent_tasks: list[dict[str, Any]]
    project_lessons: list[str]
    stale_memory_candidates: list[dict[str, Any]]
    duplicate_memory_candidates: list[dict[str, Any]]
    next_actions: list[str]


class DreamEngine:
    """Create review reports from traces, checkpoints, and memory.

    The MVP is intentionally non-mutating: it creates a report humans or future
    approval flows can inspect before changing memory.
    """

    def __init__(self, workspace_root: str | Path | None = None):
        self.workspace_root = Path(workspace_root or Path.cwd())
        self.ming_root = self.workspace_root / ".ming"

    def run(self, mode: str = "light", limit: int = 10) -> Path:
        traces = self._load_recent_json(self.ming_root / "traces", "*.json", limit)
        checkpoints = self._load_recent_json(
            self.ming_root / "checkpoints",
            "*/checkpoint.json",
            limit,
        )
        memory = MemoryStore(self.ming_root / "memory")
        memories = memory.get_all()

        report = DreamReport(
            mode=mode,
            generated_at=datetime.now().isoformat(timespec="seconds"),
            summary={
                "trace_count": len(traces),
                "checkpoint_count": len(checkpoints),
                "memory_count": len(memories),
                "stale_memory_count": len(memory.get_stale()),
            },
            recent_tasks=self._recent_tasks(traces, checkpoints),
            project_lessons=self._project_lessons(traces, checkpoints),
            stale_memory_candidates=self._stale_candidates(memory.get_stale()),
            duplicate_memory_candidates=self._duplicate_candidates(memories),
            next_actions=self._next_actions(memory.get_stale()),
        )
        return self._save_report(report)

    def _recent_tasks(
        self,
        traces: list[dict[str, Any]],
        checkpoints: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        checkpoint_by_turn = {
            checkpoint.get("turn_id"): checkpoint
            for checkpoint in checkpoints
            if checkpoint.get("turn_id")
        }
        tasks = []
        for trace in traces:
            turn_id = trace.get("turn_id", "")
            checkpoint = checkpoint_by_turn.get(turn_id, {})
            tasks.append({
                "turn_id": turn_id,
                "user_input": trace.get("user_input", ""),
                "status": "completed" if trace.get("final_output") else "incomplete",
                "tool_count": len(trace.get("tool_events") or []),
                "assessment_count": len(trace.get("assessments") or []),
                "changed_files": checkpoint.get("changed_files", []),
            })
        return tasks

    def _project_lessons(
        self,
        traces: list[dict[str, Any]],
        checkpoints: list[dict[str, Any]],
    ) -> list[str]:
        lessons: list[str] = []
        for checkpoint in checkpoints:
            for changed_file in checkpoint.get("changed_files", []):
                lessons.append(f"Changed file observed: {changed_file}")
        for trace in traces:
            final_output = " ".join(str(trace.get("final_output", "")).split())
            if final_output:
                lessons.append(f"Recent result: {self._shorten(final_output, 180)}")
        return lessons[:20]

    def _stale_candidates(self, entries: list[MemoryEntry]) -> list[dict[str, Any]]:
        return [
            {
                "name": entry.name,
                "type": entry.type,
                "description": entry.description,
                "file_path": entry.file_path,
                "stale_reason": entry.stale_reason,
                "suggestion": "人工复核后选择更新、保留降权或删除。",
            }
            for entry in entries
        ]

    def _duplicate_candidates(self, entries: list[MemoryEntry]) -> list[dict[str, Any]]:
        seen: dict[tuple[str, str], MemoryEntry] = {}
        duplicates: list[dict[str, Any]] = []
        for entry in entries:
            key = (entry.type, entry.description.strip().lower())
            if not key[1]:
                continue
            previous = seen.get(key)
            if previous:
                duplicates.append({
                    "type": entry.type,
                    "description": entry.description,
                    "memory_names": [previous.name, entry.name],
                    "suggestion": "检查是否可以合并为一条更稳定的记忆。",
                })
            else:
                seen[key] = entry
        return duplicates

    def _next_actions(self, stale_entries: list[MemoryEntry]) -> list[str]:
        actions = ["检查 Dream 报告中的 project lessons 是否值得沉淀为 project memory。"]
        if stale_entries:
            actions.append("复核待复核记忆：确认后更新内容、清除 stale 标记或删除。")
        return actions

    def _load_recent_json(self, root: Path, pattern: str, limit: int) -> list[dict[str, Any]]:
        if not root.exists():
            return []
        paths = sorted(root.glob(pattern), key=lambda path: path.stat().st_mtime)[-limit:]
        payloads: list[dict[str, Any]] = []
        for path in paths:
            try:
                payloads.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return payloads

    def _save_report(self, report: DreamReport) -> Path:
        dream_root = self.ming_root / "dreams"
        dream_root.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = dream_root / f"{timestamp}_{report.mode}.json"
        path.write_text(
            json.dumps(asdict(report), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def _shorten(self, text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 1] + "…"
