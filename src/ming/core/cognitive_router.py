"""Cognitive routing between single-agent and adversarial modes.

This module used to be named ``gate``. The new name avoids conflating cognitive
routing with permission and approval gates.
"""

import logging
import re

logger = logging.getLogger("ming")

# Rule 1: Irreversible operations
IRREVERSIBLE_PATTERNS = [
    r"\bdelete\b",
    r"\bdrop\b",
    r"\brm\s+-rf\b",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+push\s+--force\b",
    r"\btruncate\b",
    r"\bformat\b",
    r"rm\s+",
    r"remove.*permanent",
    r"destroy",
]

# Rule 2: Architectural changes
ARCHITECTURAL_PATTERNS = [
    r"system\s*prompt",
    r"core.*schema",
    r"migration",
    r"架构",
    r"重构",
    r"iron.*law",
    r"铁律",
    r"原则.*修改",
]

# Rule 3: Cross-module patterns (simplified: count file references)
MULTI_FILE_THRESHOLD = 5


class RoutingDecision:
    """Result of cognitive route evaluation."""

    def __init__(self, mode: str, triggered_rules: list[str]):
        self.mode = mode  # "single" or "adversarial"
        self.triggered_rules = triggered_rules

    @property
    def is_adversarial(self) -> bool:
        return self.mode == "adversarial"

    def __repr__(self) -> str:
        if self.is_adversarial:
            return f"CognitiveRouter→对抗 (rules: {', '.join(self.triggered_rules)})"
        return "CognitiveRouter→单核"


class CognitiveRouter:
    """Evaluate routing rules to decide single-agent vs adversarial mode."""

    def __init__(
        self,
        context_threshold: int = 30000,
        automaticity_threshold: float = 0.3,
    ):
        self.context_threshold = context_threshold
        self.automaticity_threshold = automaticity_threshold

    def evaluate(
        self,
        user_input: str,
        context_tokens: int = 0,
        automaticity: float = 0.5,
        has_historical_divergence: bool = False,
    ) -> RoutingDecision:
        """Evaluate routing rules. Any hit escalates to adversarial mode."""
        triggered: list[str] = []
        input_lower = user_input.lower()

        for pattern in IRREVERSIBLE_PATTERNS:
            if re.search(pattern, input_lower):
                triggered.append("R1:不可逆")
                break

        for pattern in ARCHITECTURAL_PATTERNS:
            if re.search(pattern, input_lower):
                triggered.append("R2:架构性")
                break

        file_refs = re.findall(r"[\w/\\]+\.\w{1,5}", user_input)
        if len(file_refs) >= MULTI_FILE_THRESHOLD:
            triggered.append(f"R3:跨模块({len(file_refs)}文件)")

        if context_tokens >= self.context_threshold:
            triggered.append(f"R4:上下文充足({context_tokens}tok)")

        explicit_keywords = [
            "再审一遍",
            "对抗",
            "反agent",
            "multi-agent",
            "independent review",
            "再检查",
        ]
        for keyword in explicit_keywords:
            if keyword in input_lower:
                triggered.append("R5:人类显式")
                break

        if has_historical_divergence:
            triggered.append("R6:历史触发")

        if automaticity < self.automaticity_threshold:
            triggered.append(f"R7:Automaticity低({automaticity:.2f})")

        mode = "adversarial" if triggered else "single"
        decision = RoutingDecision(mode=mode, triggered_rules=triggered)
        logger.info("CognitiveRouter: %s", decision)
        return decision
