"""Memory store — file-based persistent memory.

Stores memories as YAML-frontmatter markdown files (same format as Claude Code auto-memory).
Provides retrieval for context assembly.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from ming.core.llm import Message

logger = logging.getLogger("ming")


class MemoryEntry:
    """A single memory entry."""

    def __init__(
        self,
        name: str,
        description: str,
        mem_type: str,
        content: str,
        file_path: str = "",
        stale: bool = False,
        stale_reason: str = "",
    ):
        self.name = name
        self.description = description
        self.type = mem_type
        self.content = content
        self.file_path = file_path
        self.stale = stale
        self.stale_reason = stale_reason

    def to_context_string(self) -> str:
        if self.stale:
            reason = f" stale_reason={self.stale_reason}" if self.stale_reason else ""
            return (
                f"[待复核记忆:{self.type}: {self.name}{reason}] "
                f"{self.description}\n{self.content}"
            )
        return f"[{self.type}: {self.name}] {self.description}\n{self.content}"


class MemoryStore:
    """File-based memory store."""

    def __init__(self, memory_dir: str | None = None):
        self.memory_dir = Path(memory_dir) if memory_dir else Path.cwd() / ".ming" / "memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._entries: list[MemoryEntry] = []
        self._last_scan_mtime: float = 0.0
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
        self._last_scan_mtime = self._current_mtime()

    def _current_mtime(self) -> float:
        return max(
            (f.stat().st_mtime for f in self.memory_dir.glob("*.md")),
            default=0.0,
        )

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
            stale=bool(meta.get("stale", False)),
            stale_reason=str(meta.get("stale_reason", "")),
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

    def reload_if_changed(self) -> bool:
        """Reload memories if any memory file changed since the last scan."""
        current_mtime = self._current_mtime()
        if current_mtime > self._last_scan_mtime:
            self._load_all()
            return True
        return False

    async def extract_session_summary(
        self,
        messages: list[Message],
        llm_call: Any | None = None,
    ) -> list[Path]:
        """Extract memories from a session transcript."""
        if llm_call is not None:
            return await self._extract_session_summary_with_llm(messages, llm_call)
        return self.extract_session_summary_legacy(messages)

    def extract_session_summary_legacy(self, messages: list[Message]) -> list[Path]:
        """Extract simple user/project memories without an LLM."""
        saved: list[Path] = []
        combined = "\n".join(message.content for message in messages if message.content)
        if "记住" in combined or "偏好" in combined:
            saved.append(
                self.save(
                    name=f"user_summary_{datetime.now():%Y%m%d_%H%M%S}",
                    description="session user preference",
                    mem_type="user",
                    content=combined[:1000],
                )
            )
        if any(token in combined for token in ["项目结构", "src/", "src\\", "常用命令"]):
            saved.append(
                self.save(
                    name=f"project_summary_{datetime.now():%Y%m%d_%H%M%S}",
                    description="session project facts",
                    mem_type="project",
                    content=combined[:1000],
                )
            )
        return saved

    async def _extract_session_summary_with_llm(
        self,
        messages: list[Message],
        llm_call: Any,
    ) -> list[Path]:
        transcript = "\n".join(
            f"[{message.role}]: {message.content[:200]}"
            for message in messages
            if message.role in {"user", "assistant"} and message.content
        )
        if len(transcript) < 200:
            return []

        extract_messages = [
            Message(
                role="system",
                content=(
                    "从下面的对话中提取值得长期记住的信息。只提取以下类型：\n"
                    "- user: 用户的角色、知识背景、偏好\n"
                    "- feedback: 用户对 AI 行为的纠正或认可\n"
                    "- project: 项目相关的关键决策或事实\n"
                    "- reference: 外部资源的位置\n\n"
                    "如果没有值得记住的内容，输出空 JSON 数组 []。\n"
                    "输出格式：JSON 数组，每项 {type, name, description, content}。\n"
                    "name 用 kebab-case，description 一句话，content 简明扼要。\n"
                    "上限 5 条。"
                ),
            ),
            Message(role="user", content=transcript[-3000:]),
        ]

        try:
            response = await llm_call(messages=extract_messages)
            memories = json.loads(response.content)
        except Exception:
            return []

        if not isinstance(memories, list):
            return []

        saved: list[Path] = []
        for memory in memories[:5]:
            if not isinstance(memory, dict):
                continue
            name = str(memory.get("name") or "").strip()
            if not name:
                continue
            if any(entry.name == name for entry in self._entries):
                continue
            path = self.save(
                name=name,
                description=str(memory.get("description") or ""),
                mem_type=str(memory.get("type") or "project"),
                content=str(memory.get("content") or ""),
            )
            saved.append(path)
        return saved

    def mark_stale(self, path: str | Path, reason: str) -> None:
        """Mark a memory file as stale for later reconsolidation."""
        memory_path = Path(path)
        text = memory_path.read_text(encoding="utf-8", errors="replace")
        if not text.startswith("---"):
            return
        parts = text.split("---", 2)
        meta = yaml.safe_load(parts[1]) or {}
        meta["stale"] = True
        meta["stale_reason"] = reason
        frontmatter = yaml.dump(meta, allow_unicode=True, default_flow_style=False)
        memory_path.write_text(f"---\n{frontmatter}---{parts[2]}", encoding="utf-8")
        self._load_all()

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

        scored.sort(key=lambda x: (x[0], not x[1].stale), reverse=True)
        return [entry for _, entry in scored[:max_results]]

    def get_all(self) -> list[MemoryEntry]:
        """Return all memory entries."""
        return list(self._entries)

    def get_by_types(self, mem_types: list[str]) -> list[MemoryEntry]:
        """Return memories matching the requested scopes/types."""
        wanted = set(mem_types)
        entries = [entry for entry in self._entries if entry.type in wanted]
        return sorted(entries, key=lambda entry: entry.stale)

    def get_stale(self) -> list[MemoryEntry]:
        """Return memories marked as needing review."""
        return [entry for entry in self._entries if entry.stale]

    def mark_fresh(self, path: str | Path) -> None:
        """Clear stale markers after a memory has been manually reviewed."""
        memory_path = Path(path)
        text = memory_path.read_text(encoding="utf-8", errors="replace")
        if not text.startswith("---"):
            return
        parts = text.split("---", 2)
        meta = yaml.safe_load(parts[1]) or {}
        meta.pop("stale", None)
        meta.pop("stale_reason", None)
        frontmatter = yaml.dump(meta, allow_unicode=True, default_flow_style=False)
        memory_path.write_text(f"---\n{frontmatter}---{parts[2]}", encoding="utf-8")
        self._load_all()

    def get_scoped_context(self, mem_types: list[str], max_chars: int = 5000) -> str:
        """Build context from selected memory scopes only."""
        entries = self.get_by_types(mem_types)
        parts = []
        total = 0
        for entry in entries:
            text = entry.to_context_string()
            if total + len(text) > max_chars:
                break
            parts.append(text)
            total += len(text)
        return "\n\n".join(parts)

    def delete_by_type(self, mem_type: str) -> int:
        """Delete persisted memories of the given type."""
        removed = 0
        for entry in list(self._entries):
            if entry.type != mem_type or not entry.file_path:
                continue
            path = Path(entry.file_path)
            if path.exists() and path.is_file():
                path.unlink()
                removed += 1
        if removed:
            self._load_all()
        return removed

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
