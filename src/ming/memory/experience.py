"""Experience pool for lightweight historical divergence signals."""

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

DIVERGENCE_SIGNALS = {"T1_caught", "T3_error", "T4_insight", "T6_clarified", "T7_rejected"}


@dataclass
class ExperienceRecord:
    task: str
    signature: str
    tier_signal: str
    mode: str
    created_at: str


def _signature(text: str) -> str:
    """Make a small no-embedding signature from stable tokens."""
    lowered = text.lower()
    ascii_words = re.findall(r"[a-z0-9_]{2,}", lowered)
    chinese_chunks = re.findall(r"[\u4e00-\u9fff]+", lowered)
    chinese_bigrams: list[str] = []
    for chunk in chinese_chunks:
        if len(chunk) == 1:
            chinese_bigrams.append(chunk)
        else:
            chinese_bigrams.extend(chunk[i : i + 2] for i in range(len(chunk) - 1))
    useful = [*ascii_words, *chinese_bigrams]
    return " ".join(useful[:8])


def _overlaps(left: str, right: str) -> bool:
    left_terms = set(left.split())
    right_terms = set(right.split())
    if not left_terms or not right_terms:
        return False
    return bool(left_terms & right_terms)


class ExperienceStore:
    """Append-only experience records with simple keyword retrieval."""

    def __init__(self, store_path: str | Path | None = None):
        self.store_path = (
            Path(store_path) if store_path else Path.cwd() / ".ming" / "experience.jsonl"
        )
        self.store_path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, task: str, tier_signal: str, mode: str) -> None:
        record = ExperienceRecord(
            task=task,
            signature=_signature(task),
            tier_signal=tier_signal,
            mode=mode,
            created_at=datetime.now().isoformat(timespec="seconds"),
        )
        with self.store_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")

    def has_historical_divergence(self, task: str, limit: int = 200) -> bool:
        query_sig = _signature(task)
        if not query_sig or not self.store_path.exists():
            return False

        lines = self.store_path.read_text(encoding="utf-8").splitlines()[-limit:]
        for line in reversed(lines):
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if data.get("tier_signal") not in DIVERGENCE_SIGNALS:
                continue
            if _overlaps(query_sig, data.get("signature", "")):
                return True
        return False
