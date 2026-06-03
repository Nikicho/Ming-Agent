"""Memory store — file-based persistent memory.

Stores memories as YAML-frontmatter markdown files (same format as Claude Code auto-memory).
Provides retrieval for context assembly.
"""

import logging
import os
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("ming")


class MemoryEntry:
    """A single memory entry."""

    def __init__(self, name: str, description: str, mem_type: str, content: str, file_path: str = ""):
        self.name = name
        self.description = description
        self.type = mem_type
        self.content = content
        self.file_path = file_path

    def to_context_string(self) -> str:
        return f"[{self.type}: {self.name}] {self.description}\n{self.content}"


class MemoryStore:
    """File-based memory store."""

    def __init__(self, memory_dir: str | None = None):
        self.memory_dir = Path(memory_dir) if memory_dir else Path.cwd() / ".ming" / "memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._entries: list[MemoryEntry] = []
        self._load_all()

    def _load_all(self) -> None:
        """Load all memory files from disk."""
        self._entries = []
        for f in self.memory_dir.glob("*.md"):
            try:
                entry = self._parse_file(f)
                if entry:
                    self._entries.append(entry)
            except Exception as e:
                logger.warning(f"Failed to load memory {f}: {e}")

    def _parse_file(self, path: Path) -> MemoryEntry | None:
        """Parse a memory markdown file with YAML frontmatter."""
        text = path.read_text(encoding="utf-8", errors="replace")
        if not text.startswith("---"):
            return None

        parts = text.split("---", 2)
        if len(parts) < 3:
            return None

        try:
            meta = yaml.safe_load(parts[1])
        except yaml.YAMLError:
            return None

        if not meta or not isinstance(meta, dict):
            return None

        return MemoryEntry(
            name=meta.get("name", path.stem),
            description=meta.get("description", ""),
            mem_type=meta.get("type", "unknown"),
            content=parts[2].strip(),
            file_path=str(path),
        )

    def save(self, name: str, description: str, mem_type: str, content: str) -> Path:
        """Save a memory entry to disk."""
        filename = name.replace(" ", "_").replace("/", "_") + ".md"
        path = self.memory_dir / filename

        frontmatter = yaml.dump(
            {"name": name, "description": description, "type": mem_type},
            allow_unicode=True,
            default_flow_style=False,
        )

        text = f"---\n{frontmatter}---\n\n{content}\n"
        path.write_text(text, encoding="utf-8")

        self._load_all()
        logger.info(f"Memory saved: {name} ({mem_type})")
        return path

    def search(self, query: str, max_results: int = 5) -> list[MemoryEntry]:
        """Simple keyword search across memories."""
        query_lower = query.lower()
        scored: list[tuple[int, MemoryEntry]] = []

        for entry in self._entries:
            score = 0
            searchable = f"{entry.name} {entry.description} {entry.content}".lower()
            for word in query_lower.split():
                if word in searchable:
                    score += 1
            if score > 0:
                scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [entry for _, entry in scored[:max_results]]

    def get_all(self) -> list[MemoryEntry]:
        """Return all memory entries."""
        return list(self._entries)

    def get_session_context(self, max_chars: int = 5000) -> str:
        """Build session-layer context string from all memories."""
        if not self._entries:
            return ""

        parts = []
        total = 0
        for entry in self._entries:
            text = entry.to_context_string()
            if total + len(text) > max_chars:
                break
            parts.append(text)
            total += len(text)

        return "\n\n".join(parts)
