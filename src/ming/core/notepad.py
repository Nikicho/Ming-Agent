"""Per-turn notepad for scratch observations."""

from pathlib import Path


class NotepadStore:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root else Path.cwd() / ".ming" / "scratch"
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, turn_id: str, user_input: str) -> Path:
        turn_dir = self.root / turn_id
        turn_dir.mkdir(parents=True, exist_ok=True)
        path = turn_dir / "notes.md"
        path.write_text(
            "# Turn Notes\n\n"
            "## User Request\n\n"
            f"{user_input}\n\n"
            "## Assumptions\n\n"
            "## Evidence\n\n"
            "## Blockers\n\n"
            "## Tool Observations\n",
            encoding="utf-8",
        )
        return path

    def append(self, path: Path, text: str) -> None:
        with path.open("a", encoding="utf-8") as f:
            f.write(f"\n{text}\n")

    def add_assumption(self, path: Path, text: str) -> None:
        self._append_under(path, "## Assumptions", f"- {text}")

    def add_evidence(self, path: Path, source: str, text: str) -> None:
        self._append_under(path, "## Evidence", f"- {source}: {text}")

    def add_blocker(self, path: Path, text: str) -> None:
        self._append_under(path, "## Blockers", f"- {text}")

    def add_tool_observation(self, path: Path, text: str) -> None:
        self._append_under(path, "## Tool Observations", f"- {text}")

    def summary(self, path: Path, max_chars: int = 2000) -> str:
        if not path.exists():
            return ""
        text = path.read_text(encoding="utf-8", errors="replace")
        return text[:max_chars].rstrip()

    def _append_under(self, path: Path, heading: str, line: str) -> None:
        if not path.exists():
            return
        text = path.read_text(encoding="utf-8", errors="replace")
        marker = f"{heading}\n"
        if marker not in text:
            self.append(path, f"\n{heading}\n{line}")
            return
        text = text.replace(marker, f"{marker}\n{line}\n", 1)
        path.write_text(text, encoding="utf-8")
