"""Metadata-only skill index and tool need proposals."""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


class SkillIndex:
    """Store skill metadata without loading skill bodies into core context."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "skills.json"
        if not self.path.exists():
            self.path.write_text("[]", encoding="utf-8")

    def register(
        self,
        name: str,
        description: str,
        trust_level: str,
        allowed_tools: list[str],
    ) -> None:
        entries = self._read()
        entries = [entry for entry in entries if entry.get("name") != name]
        entries.append(
            {
                "name": name,
                "description": description,
                "trust_level": trust_level,
                "allowed_tools": allowed_tools,
            }
        )
        self.path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")

    def search(self, query: str) -> list[dict]:
        terms = set(query.lower().split())
        matches = []
        for entry in self._read():
            haystack = f"{entry.get('name', '')} {entry.get('description', '')}".lower()
            if any(term in haystack for term in terms):
                matches.append(entry)
        return matches

    def _read(self) -> list[dict]:
        return json.loads(self.path.read_text(encoding="utf-8"))


@dataclass
class ToolNeedProposal:
    need: str
    reason: str
    tests: list[str] = field(default_factory=list)
    status: str = "needs_human_approval"

    def to_dict(self) -> dict:
        return asdict(self)
