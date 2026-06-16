"""Cost-aware evaluation summaries."""

from __future__ import annotations

from typing import Any


def summarize_trace_budget(
    trace: dict[str, Any],
    max_cost_usd: float | None = None,
    max_llm_calls: int | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """Summarize trace cost and flag budget breaches."""
    metrics = trace.get("session_metrics") or {}
    prompt_tokens = int(metrics.get("total_prompt_tokens") or 0)
    completion_tokens = int(metrics.get("total_completion_tokens") or 0)
    total_tokens = prompt_tokens + completion_tokens
    total_cost = float(metrics.get("total_cost_usd") or 0.0)
    total_llm_calls = int(metrics.get("total_llm_calls") or 0)

    reasons: list[str] = []
    if max_cost_usd is not None and total_cost > max_cost_usd:
        reasons.append("cost")
    if max_llm_calls is not None and total_llm_calls > max_llm_calls:
        reasons.append("llm_calls")
    if max_tokens is not None and total_tokens > max_tokens:
        reasons.append("tokens")

    return {
        "total_cost_usd": total_cost,
        "total_llm_calls": total_llm_calls,
        "total_tokens": total_tokens,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "over_budget": bool(reasons),
        "reasons": reasons,
    }

