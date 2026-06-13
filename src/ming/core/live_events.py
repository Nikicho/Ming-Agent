"""Durable live event stream for local UI and CLI coordination."""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
DEFAULT_MAX_EVENTS = 1000

SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)[^\s,;]+"),
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
]


class LiveEventStore:
    """Append-only JSONL event store under ``.ming/live``."""

    def __init__(self, root: str | Path | None = None, max_events: int = DEFAULT_MAX_EVENTS):
        self.root = Path(root) if root else Path.cwd() / ".ming" / "live"
        self.path = self.root / "events.jsonl"
        self.max_events = max_events
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
                "schema_version": SCHEMA_VERSION,
                "seq": self._next_seq(),
                "time": datetime.now().isoformat(timespec="seconds"),
                "turn_id": turn_id,
                "stage": stage,
                "message": self._sanitize(message),
                "detail": self._sanitize(detail),
                "type": event_type,
            }
            self.root.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            self._rotate_if_needed()
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

    def _rotate_if_needed(self) -> None:
        if self.max_events <= 0:
            return
        events = self._read_all()
        if len(events) <= self.max_events:
            return
        kept = events[-self.max_events :]
        with self.path.open("w", encoding="utf-8") as handle:
            for event in kept:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _sanitize(self, value: str) -> str:
        text = str(value)
        for pattern in SECRET_PATTERNS:
            text = pattern.sub(self._redacted_match, text)
        return text

    def _redacted_match(self, match: re.Match[str]) -> str:
        if match.lastindex:
            return f"{match.group(1)}[redacted]"
        return "[redacted]"
