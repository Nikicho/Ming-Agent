"""Local recovery helpers for file tool changes."""

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class FileSnapshot:
    snapshot_id: str
    path: str
    existed: bool
    content: str = ""


class FileSnapshotStore:
    """Persist pre-change file states so the latest file tool change can roll back."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._stack: list[Path] = []

    def snapshot(self, path: str | Path) -> Path:
        target = Path(path)
        snapshot = FileSnapshot(
            snapshot_id=datetime.now().strftime("%Y%m%d_%H%M%S_%f"),
            path=str(target),
            existed=target.exists(),
            content=target.read_text(encoding="utf-8", errors="replace")
            if target.exists() and target.is_file()
            else "",
        )
        snapshot_path = self.root / f"{snapshot.snapshot_id}.json"
        snapshot_path.write_text(
            json.dumps(asdict(snapshot), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._stack.append(snapshot_path)
        return snapshot_path

    def rollback_latest(self) -> dict[str, int | str]:
        path = self._latest_snapshot_path()
        if path is None:
            return {"rolled_back": 0, "reason": "no snapshot"}

        data = json.loads(path.read_text(encoding="utf-8"))
        target = Path(data["path"])
        if data["existed"]:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(data.get("content", ""), encoding="utf-8")
        elif target.exists() and target.is_file():
            target.unlink()

        path.unlink(missing_ok=True)
        if path in self._stack:
            self._stack.remove(path)
        return {"rolled_back": 1, "path": str(target)}

    def _latest_snapshot_path(self) -> Path | None:
        while self._stack:
            path = self._stack[-1]
            if path.exists():
                return path
            self._stack.pop()

        snapshots = sorted(self.root.glob("*.json"), key=lambda p: p.stat().st_mtime)
        return snapshots[-1] if snapshots else None
