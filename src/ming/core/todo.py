"""Minimal per-turn TODO state."""

import re
from dataclasses import asdict, dataclass


@dataclass
class TodoItem:
    text: str
    status: str = "pending"


class TodoState:
    def __init__(self, items: list[TodoItem]):
        self.items = items

    @classmethod
    def from_user_input(cls, user_input: str) -> "TodoState":
        parts = [
            part.strip(" ，,。.;；")
            for part in re.split(r"然后|并且|并|再|，|,|；|;", user_input)
            if part.strip(" ，,。.;；")
        ]
        if not parts:
            parts = [user_input.strip() or "处理用户请求"]
        items = [TodoItem(text=part[:120]) for part in parts]
        items[0].status = "in_progress"
        return cls(items)

    def mark_step_completed(self, signal: str = "") -> None:
        """Mark the current active step done and advance the next pending step."""
        for index, item in enumerate(self.items):
            if item.status == "in_progress":
                item.status = "completed"
                self._start_next(index + 1)
                return
        for index, item in enumerate(self.items):
            if item.status == "pending":
                item.status = "completed"
                self._start_next(index + 1)
                return

    def complete_all(self) -> None:
        for item in self.items:
            item.status = "completed"

    def to_context(self) -> str:
        return "\n".join(f"- [{item.status}] {item.text}" for item in self.items)

    def to_dict(self) -> dict:
        return {"items": [asdict(item) for item in self.items]}

    def _start_next(self, start_index: int) -> None:
        for item in self.items[start_index:]:
            if item.status == "pending":
                item.status = "in_progress"
                return
