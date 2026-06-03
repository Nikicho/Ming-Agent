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
        path.write_text(f"# Turn Notes\n\n## User Request\n\n{user_input}\n", encoding="utf-8")
        return path

    def append(self, path: Path, text: str) -> None:
        with path.open("a", encoding="utf-8") as f:
            f.write(f"\n{text}\n")
