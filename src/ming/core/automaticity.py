"""Automaticity continuous spectrum — behavior pattern management.

Tracks how "automatic" each type of task is based on historical outcomes.
High automaticity = habitual (直接执行), Low = deliberate (完整推理).
"""

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("ming")


class BehaviorPattern:
    """A single behavior pattern with its automaticity score."""

    def __init__(
        self,
        name: str,
        keywords: list[str],
        automaticity: float = 0.5,
        success_count: int = 0,
        failure_count: int = 0,
    ):
        self.name = name
        self.keywords = keywords
        self.automaticity = max(0.0, min(1.0, automaticity))
        self.success_count = success_count
        self.failure_count = failure_count

    def matches(self, text: str) -> bool:
        """Check if this pattern matches the given text."""
        text_lower = text.lower()
        return any(kw.lower() in text_lower for kw in self.keywords)

    def update(self, tier_signal: str) -> None:
        """Update automaticity based on tier signal feedback.

        Tier signals and their deltas (from design doc 1.3):
          T0_success:  ↑↑   (+0.10)  — no verification needed
          T1_caught:   ↓    (-0.05)  — CoVe found hallucination
          T2_hit:      ↓微  (-0.02)  — bias checklist triggered
          T3_error:    ↓中  (-0.08)  — fact checker found error
          T3_pass:     ↑    (+0.05)  — fact checker passed
          T4_agree:    ↑↑↑  (+0.15)  — β independently agreed
          T4_insight:  ↓↓↓  (-0.15)  — β found blind spot
          T6_clarified:↓↓   (-0.12)  — needed divergence resolution
          T7_rejected: ↓↓↓↓ (-0.20)  — human rejected
        """
        deltas = {
            "T0_success": 0.10,
            "T1_caught": -0.05,
            "T2_hit": -0.02,
            "T3_error": -0.08,
            "T3_pass": 0.05,
            "T4_agree": 0.15,
            "T4_insight": -0.15,
            "T6_clarified": -0.12,
            "T7_rejected": -0.20,
        }

        delta = deltas.get(tier_signal, 0)
        if delta == 0:
            return

        old = self.automaticity
        self.automaticity = max(0.0, min(1.0, self.automaticity + delta))

        if delta > 0:
            self.success_count += 1
        else:
            self.failure_count += 1

        logger.debug(
            f"Automaticity [{self.name}]: {old:.2f} → {self.automaticity:.2f} "
            f"(signal={tier_signal}, Δ={delta:+.2f})"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "keywords": self.keywords,
            "automaticity": round(self.automaticity, 4),
            "success_count": self.success_count,
            "failure_count": self.failure_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BehaviorPattern":
        return cls(**data)


class AutomaticityStore:
    """Persistent store for behavior patterns and their automaticity scores."""

    def __init__(self, store_path: str | None = None):
        self.store_path = (
            Path(store_path) if store_path else Path.cwd() / ".ming" / "automaticity.json"
        )
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.patterns: list[BehaviorPattern] = []
        self._load()

    def _load(self) -> None:
        if self.store_path.exists():
            try:
                data = json.loads(self.store_path.read_text(encoding="utf-8"))
                self.patterns = [BehaviorPattern.from_dict(p) for p in data]
                logger.debug(f"Loaded {len(self.patterns)} behavior patterns")
            except Exception as e:
                logger.warning(f"Failed to load automaticity store: {e}")
                self.patterns = []
        else:
            self.patterns = self._default_patterns()
            self._save()

    def _save(self) -> None:
        data = [p.to_dict() for p in self.patterns]
        self.store_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _default_patterns(self) -> list[BehaviorPattern]:
        """Seed patterns with reasonable defaults."""
        return [
            BehaviorPattern("code_edit", ["修改", "改代码", "fix", "bug", "edit", "refactor"], 0.5),
            BehaviorPattern("code_write", ["写代码", "创建", "create", "implement", "新建"], 0.5),
            BehaviorPattern("code_review", ["review", "审查", "检查", "code review"], 0.3),
            BehaviorPattern(
                "architecture",
                ["架构", "设计", "architecture", "design", "重构"],
                0.2,
            ),
            BehaviorPattern("testing", ["测试", "test", "pytest", "unittest"], 0.5),
            BehaviorPattern("git_ops", ["git", "commit", "push", "merge", "branch"], 0.6),
            BehaviorPattern("file_ops", ["读文件", "read", "查看", "cat", "ls", "目录"], 0.8),
            BehaviorPattern("explanation", ["解释", "explain", "为什么", "怎么", "what is"], 0.7),
        ]

    def match(self, text: str) -> BehaviorPattern | None:
        """Find the best matching behavior pattern for the given text."""
        matches = [p for p in self.patterns if p.matches(text)]
        if not matches:
            return None
        # Return lowest automaticity match (most cautious)
        return min(matches, key=lambda p: p.automaticity)

    def get_automaticity(self, text: str) -> float:
        """Get automaticity score for a task. Returns 0.5 (cautious default) if no match."""
        pattern = self.match(text)
        return pattern.automaticity if pattern else 0.5

    def update(self, text: str, tier_signal: str) -> None:
        """Update the matching pattern with a tier signal."""
        pattern = self.match(text)
        if pattern:
            pattern.update(tier_signal)
            self._save()

    def add_pattern(self, name: str, keywords: list[str], automaticity: float = 0.5) -> None:
        """Add a new behavior pattern."""
        self.patterns.append(BehaviorPattern(name, keywords, automaticity))
        self._save()
