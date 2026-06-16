"""Cost-aware LLM judge selection for structured Ming traces."""

from __future__ import annotations

from typing import Any


def select_judges_for_turn(turn: dict[str, Any]) -> list[str]:
    """Select only the judges that match the turn shape.

    The testing architecture intentionally avoids running a full judge panel on
    every turn. Deterministic checks and trace shape decide which expensive
    judge prompts are worth running.
    """
    judges = ["gate_judge"]
    execution = turn.get("execution")

    if execution == "adversarial" or turn.get("adversarial"):
        judges.extend(["gamma_output_judge", "adversarial_value_judge"])
    elif _has_tool_calls(turn):
        judges.append("tool_use_judge")

    if turn.get("compaction_events") or turn.get("compaction"):
        judges.append("compaction_judge")

    return judges


def _has_tool_calls(turn: dict[str, Any]) -> bool:
    single_agent = turn.get("single_agent") or {}
    for step in single_agent.get("steps") or []:
        if step.get("tool_calls"):
            return True
    return False

