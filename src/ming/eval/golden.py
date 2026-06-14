"""Golden conversation scenario loading."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class GoldenTurn:
    user: str
    expect: dict[str, Any]


@dataclass(frozen=True)
class GoldenConversation:
    id: str
    description: str
    tags: list[str]
    turns: list[GoldenTurn]
    source_path: Path


def load_golden_conversation(path: str | Path) -> GoldenConversation:
    """Load a golden conversation YAML file into a typed object."""
    source_path = Path(path)
    payload = yaml.safe_load(source_path.read_text(encoding="utf-8")) or {}
    turns = [
        GoldenTurn(
            user=str(turn.get("user", "")),
            expect=dict(turn.get("expect") or {}),
        )
        for turn in payload.get("turns", [])
    ]
    return GoldenConversation(
        id=str(payload.get("id") or source_path.stem),
        description=str(payload.get("description") or ""),
        tags=[str(tag) for tag in payload.get("tags", [])],
        turns=turns,
        source_path=source_path,
    )

