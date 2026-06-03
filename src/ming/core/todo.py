"""Minimal per-turn TODO state."""

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
        return cls([TodoItem(text=f"处理用户请求：{user_input[:120]}", status="in_progress")])

    def complete_all(self) -> None:
        for item in self.items:
            item.status = "completed"

    def to_context(self) -> str:
        return "\n".join(f"- [{item.status}] {item.text}" for item in self.items)

    def to_dict(self) -> dict:
        return {"items": [asdict(item) for item in self.items]}
