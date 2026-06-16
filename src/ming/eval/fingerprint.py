"""Behavior fingerprinting for Ming session traces."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def behavior_fingerprint(trace: dict[str, Any]) -> str:
    """Return a stable hash over behaviorally important trace fields."""
    canonical = [_turn_signature(turn) for turn in trace.get("turns", [])]
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "bf_" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _turn_signature(turn: dict[str, Any]) -> dict[str, Any]:
    single_agent = turn.get("single_agent") or {}
    tool_sequence: list[dict[str, Any]] = []
    for step in single_agent.get("steps") or []:
        for tool_call in step.get("tool_calls") or []:
            tool_sequence.append(
                {
                    "name": tool_call.get("name"),
                    "loop_status": tool_call.get("loop_status"),
                    "is_error": bool(tool_call.get("result_is_error")),
                }
            )
    return {
        "execution": turn.get("execution"),
        "gate_mode": (turn.get("gate") or {}).get("mode"),
        "triggered_rules": (turn.get("gate") or {}).get("triggered_rules") or [],
        "tool_sequence": tool_sequence,
        "l5_ceiling_hit": single_agent.get("l5_ceiling_hit"),
        "tier_signal": (turn.get("feedback") or {}).get("tier_signal"),
    }

