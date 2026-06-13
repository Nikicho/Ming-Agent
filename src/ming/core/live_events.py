"""Durable live event stream for local UI and CLI coordination."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any


class LiveEventStore:
    """Append-only JSONL event store under ``.ming/live``."""

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root else Path.cwd() / ".ming" / "live"
        self.path = self.root / "events.jsonl"
        self._lock = threading.Lock()

    def append(
        self,
        stage: str,
        message: str,
        turn_id: str = "",
        detail: str = "",
        event_type: str = "progress",
    ) -> dict[str, Any]:
        """Append a live event and return the persisted payload."""
        with self._lock:
            event = {
                "seq": self._next_seq(),
                "time": datetime.now().isoformat(timespec="seconds"),
                "turn_id": turn_id,
                "stage": stage,
                "message": message,
                "detail": detail,
                "type": event_type,
            }
            self.root.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            return event

    def since(self, seq: int = 0) -> list[dict[str, Any]]:
        """Return all events with sequence greater than ``seq``."""
        return [event for event in self._read_all() if event.get("seq", 0) > seq]

    def tail(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the latest events."""
        if limit <= 0:
            return []
        return self._read_all()[-limit:]

    def clear(self) -> None:
        """Clear the event log."""
        self.root.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")

    def _next_seq(self) -> int:
        events = self._read_all()
        if not events:
            return 1
        return max(int(event.get("seq", 0)) for event in events) + 1

    def _read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                events.append(payload)
        return events
