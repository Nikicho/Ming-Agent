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

    def __init__(
        self,
        mode: str,
        triggered_rules: list[str],
        all_rules_evaluated: dict[str, bool] | None = None,
    ):
        self.mode = mode  # "single" or "adversarial"
        self.triggered_rules = triggered_rules
        self.all_rules_evaluated = all_rules_evaluated or {}

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
        all_rules: dict[str, bool] = {}
        input_lower = user_input.lower()

        r1_hit = any(re.search(p, input_lower) for p in IRREVERSIBLE_PATTERNS)
        all_rules["R1_irreversibility"] = r1_hit
        if r1_hit:
            triggered.append("R1:不可逆")

        r2_hit = any(re.search(p, input_lower) for p in ARCHITECTURAL_PATTERNS)
        all_rules["R2_architectural"] = r2_hit
        if r2_hit:
            triggered.append("R2:架构性")

        file_refs = re.findall(r"[\w/\\]+\.\w{1,5}", user_input)
        r3_hit = len(file_refs) >= MULTI_FILE_THRESHOLD
        all_rules["R3_cross_module"] = r3_hit
        if r3_hit:
            triggered.append(f"R3:跨模块({len(file_refs)}文件)")

        r4_hit = context_tokens >= self.context_threshold
        all_rules["R4_context_rich"] = r4_hit
        if r4_hit:
            triggered.append(f"R4:上下文充足({context_tokens}tok)")

        explicit_keywords = [
            "再审一遍",
            "对抗",
            "反agent",
            "multi-agent",
            "independent review",
            "再检查",
        ]
        r5_hit = any(kw in input_lower for kw in explicit_keywords)
        all_rules["R5_explicit_user"] = r5_hit
        if r5_hit:
            triggered.append("R5:人类显式")

        all_rules["R6_historical_divergence"] = has_historical_divergence
        if has_historical_divergence:
            triggered.append("R6:历史触发")

        r7_hit = automaticity < self.automaticity_threshold
        all_rules["R7_low_automaticity"] = r7_hit
        if r7_hit:
            triggered.append(f"R7:Automaticity低({automaticity:.2f})")

        mode = "adversarial" if triggered else "single"
        decision = RoutingDecision(
            mode=mode,
            triggered_rules=triggered,
            all_rules_evaluated=all_rules,
        )
        logger.info("CognitiveRouter: %s", decision)
        return decision
