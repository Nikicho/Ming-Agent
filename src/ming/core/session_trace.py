"""Structured session trace — ming-trace-v1.

Records the complete execution trace of a Ming session in a structured JSON
format designed for evaluation, debugging, and regression testing.

Design decisions and rationale documented in:
  Obsidian Vault/自主进化Agent研究/Ming 测试架构设计文档.md (Chapter 1)

CheckpointStore (trace.py) handles dialog context snapshots for /resume.
SessionTrace is the structured evaluation trace wrapping all turns in a session.
"""

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("ming")

SCHEMA_VERSION = "ming-trace-v1"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class LLMCallMetrics:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0

    @classmethod
    def from_usage(cls, usage: dict[str, int], latency_ms: int = 0) -> "LLMCallMetrics":
        return cls(
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            latency_ms=latency_ms,
        )


@dataclass
class ToolCallTrace:
    id: str
    name: str
    arguments: str
    loop_status: str  # "not_checked" after L1 fingerprint checks were removed
    consecutive_identical: int
    result_output_length: int = 0
    result_is_error: bool = False
    latency_ms: int = 0


@dataclass
class StepTrace:
    step_id: int
    iteration: int
    response_content_length: int = 0
    tool_calls: list[ToolCallTrace] = field(default_factory=list)
    is_final: bool = False
    metrics: LLMCallMetrics = field(default_factory=LLMCallMetrics)


@dataclass
class T1CheckTrace:
    ran: bool = False
    draft_changed: bool = False
    tier_signal: str = ""
    metrics: LLMCallMetrics = field(default_factory=LLMCallMetrics)


@dataclass
class T3CheckTrace:
    ran: bool = False
    passed: bool = False
    tier_signal: str = ""
    repair_attempted: bool = False
    metrics: LLMCallMetrics = field(default_factory=LLMCallMetrics)


@dataclass
class SingleAgentTrace:
    steps: list[StepTrace] = field(default_factory=list)
    total_iterations: int = 0
    t1_self_check: T1CheckTrace = field(default_factory=T1CheckTrace)
    t3_fact_check: T3CheckTrace = field(default_factory=T3CheckTrace)
    l5_ceiling_hit: str | None = None  # "iteration_limit", "timeout", or None


@dataclass
class AdversarialAgentTrace:
    alpha_output_length: int = 0
    alpha_metrics: LLMCallMetrics = field(default_factory=LLMCallMetrics)
    beta_output_length: int = 0
    beta_metrics: LLMCallMetrics = field(default_factory=LLMCallMetrics)
    gamma_phase1_consistency: str = ""
    gamma_phase1_metrics: LLMCallMetrics = field(default_factory=LLMCallMetrics)
    gamma_phase2_ran: bool = False
    gamma_phase2_metrics: LLMCallMetrics = field(default_factory=LLMCallMetrics)
    tier_signal: str = ""
    total_latency_ms: int = 0


@dataclass
class GateTrace:
    mode: str = ""
    triggered_rules: list[str] = field(default_factory=list)
    all_rules_evaluated: dict[str, bool] = field(default_factory=dict)
    automaticity_score: float = 0.0
    context_tokens_at_gate: int = 0


@dataclass
class FeedbackTrace:
    automaticity_before: float = 0.0
    automaticity_after: float = 0.0
    tier_signal: str = ""
    experience_recorded: bool = False


@dataclass
class TurnMetrics:
    total_llm_calls: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_latency_ms: int = 0
    estimated_cost_usd: float = 0.0


@dataclass
class CompactionEvent:
    timestamp: str = ""
    trigger: str = ""  # "threshold", "safety", "manual"
    before_tokens: int = 0
    after_tokens: int = 0
    compression_ratio: float = 0.0
    messages_before: int = 0
    messages_after: int = 0
    phase1_messages_pruned: int = 0
    phase2_ran: bool = False
    phase2_metrics: LLMCallMetrics = field(default_factory=LLMCallMetrics)


@dataclass
class TurnTrace:
    turn_id: str
    timestamp: str
    user_input: str
    gate: GateTrace = field(default_factory=GateTrace)
    execution: str = ""  # "single" or "adversarial"
    single_agent: SingleAgentTrace | None = None
    adversarial: AdversarialAgentTrace | None = None
    final_output: str = ""
    final_output_length: int = 0
    feedback: FeedbackTrace = field(default_factory=FeedbackTrace)
    turn_metrics: TurnMetrics = field(default_factory=TurnMetrics)
    error: str | None = None  # set if the turn ended due to an error


@dataclass
class SessionMetrics:
    total_turns: int = 0
    adversarial_turns: int = 0
    single_turns: int = 0
    error_turns: int = 0
    compaction_count: int = 0
    total_llm_calls: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_cost_usd: float = 0.0
    wall_clock_seconds: float = 0.0


# ---------------------------------------------------------------------------
# Session trace recorder
# ---------------------------------------------------------------------------

class SessionTrace:
    """Accumulates structured trace data across an entire session."""

    def __init__(self, model: str = "", agent_version: str = ""):
        self.session_id = datetime.now().strftime("s_%Y%m%d_%H%M%S")
        self.started_at = datetime.now().isoformat(timespec="seconds")
        self.model = model
        self.agent_version = agent_version
        self.turns: list[TurnTrace] = []
        self.compaction_events: list[CompactionEvent] = []
        self._session_start_time = time.monotonic()

        # Current turn state (populated during a turn, flushed on finish)
        self._current_turn: TurnTrace | None = None
        self._turn_llm_calls: int = 0
        self._turn_prompt_tokens: int = 0
        self._turn_completion_tokens: int = 0
        self._turn_latency_ms: int = 0
        self._step_counter: int = 0

    # -- Turn lifecycle --

    def begin_turn(self, turn_id: str, user_input: str) -> None:
        self._current_turn = TurnTrace(
            turn_id=turn_id,
            timestamp=datetime.now().isoformat(timespec="seconds"),
            user_input=user_input,
        )
        self._turn_llm_calls = 0
        self._turn_prompt_tokens = 0
        self._turn_completion_tokens = 0
        self._turn_latency_ms = 0
        self._step_counter = 0

    def finish_turn(self, final_output: str) -> TurnTrace | None:
        turn = self._current_turn
        if turn is None:
            return None

        turn.final_output = final_output
        turn.final_output_length = len(final_output)
        turn.turn_metrics = TurnMetrics(
            total_llm_calls=self._turn_llm_calls,
            total_prompt_tokens=self._turn_prompt_tokens,
            total_completion_tokens=self._turn_completion_tokens,
            total_latency_ms=self._turn_latency_ms,
            estimated_cost_usd=self._estimate_cost(),
        )
        self.turns.append(turn)
        self._current_turn = None
        return turn

    def fail_turn(self, error: str) -> None:
        if self._current_turn is not None:
            self._current_turn.error = error
            self.finish_turn("")

    # -- Gate / routing --

    def record_gate(
        self,
        mode: str,
        triggered_rules: list[str],
        all_rules: dict[str, bool],
        automaticity: float,
        context_tokens: int,
    ) -> None:
        if self._current_turn is None:
            return
        self._current_turn.gate = GateTrace(
            mode=mode,
            triggered_rules=list(triggered_rules),
            all_rules_evaluated=dict(all_rules),
            automaticity_score=automaticity,
            context_tokens_at_gate=context_tokens,
        )
        self._current_turn.execution = mode

    # -- Single agent path --

    def init_single_path(self) -> None:
        if self._current_turn is None:
            return
        self._current_turn.execution = "single"
        self._current_turn.single_agent = SingleAgentTrace()

    def record_llm_call(self, usage: dict[str, int], latency_ms: int) -> None:
        """Record metrics for any LLM call (single, T1, T3, etc.)."""
        self._turn_llm_calls += 1
        self._turn_prompt_tokens += usage.get("prompt_tokens", 0)
        self._turn_completion_tokens += usage.get("completion_tokens", 0)
        self._turn_latency_ms += latency_ms

    def begin_step(self, iteration: int) -> None:
        self._step_counter += 1

    def finish_step(
        self,
        iteration: int,
        response_content_length: int,
        tool_calls: list[ToolCallTrace],
        is_final: bool,
        metrics: LLMCallMetrics,
    ) -> None:
        if self._current_turn is None or self._current_turn.single_agent is None:
            return
        step = StepTrace(
            step_id=self._step_counter,
            iteration=iteration,
            response_content_length=response_content_length,
            tool_calls=tool_calls,
            is_final=is_final,
            metrics=metrics,
        )
        self._current_turn.single_agent.steps.append(step)
        self._current_turn.single_agent.total_iterations = iteration

    def record_l5_ceiling(self, reason: str) -> None:
        if self._current_turn and self._current_turn.single_agent:
            self._current_turn.single_agent.l5_ceiling_hit = reason

    def record_t1_check(
        self,
        draft_changed: bool,
        tier_signal: str,
        metrics: LLMCallMetrics,
    ) -> None:
        if self._current_turn and self._current_turn.single_agent:
            self._current_turn.single_agent.t1_self_check = T1CheckTrace(
                ran=True,
                draft_changed=draft_changed,
                tier_signal=tier_signal,
                metrics=metrics,
            )

    def record_t3_check(
        self,
        passed: bool,
        tier_signal: str,
        repair_attempted: bool,
        metrics: LLMCallMetrics,
    ) -> None:
        if self._current_turn and self._current_turn.single_agent:
            self._current_turn.single_agent.t3_fact_check = T3CheckTrace(
                ran=True,
                passed=passed,
                tier_signal=tier_signal,
                repair_attempted=repair_attempted,
                metrics=metrics,
            )

    # -- Adversarial path --

    def record_adversarial(self, trace: AdversarialAgentTrace) -> None:
        if self._current_turn is None:
            return
        self._current_turn.execution = "adversarial"
        self._current_turn.adversarial = trace

    # -- Feedback --

    def record_feedback(
        self,
        automaticity_before: float,
        automaticity_after: float,
        tier_signal: str,
    ) -> None:
        if self._current_turn is None:
            return
        self._current_turn.feedback = FeedbackTrace(
            automaticity_before=automaticity_before,
            automaticity_after=automaticity_after,
            tier_signal=tier_signal,
            experience_recorded=True,
        )

    # -- Compaction --

    def record_compaction(self, event: CompactionEvent) -> None:
        self.compaction_events.append(event)

    # -- Cost estimation --
    # Problem: LiteLLM cost data is model-dependent and often unavailable for
    # non-OpenAI models (like DeepSeek). We can't rely on it.
    # Solution: Use a rough estimate based on token counts with a configurable
    # price-per-token. Default to DeepSeek V4 Flash pricing.
    # This is marked "estimated" in the output to avoid misleading.

    PRICE_PER_1K_INPUT = 0.0001   # rough DeepSeek V4 Flash pricing
    PRICE_PER_1K_OUTPUT = 0.0003

    def _estimate_cost(self) -> float:
        input_cost = (self._turn_prompt_tokens / 1000) * self.PRICE_PER_1K_INPUT
        output_cost = (self._turn_completion_tokens / 1000) * self.PRICE_PER_1K_OUTPUT
        return round(input_cost + output_cost, 6)

    # -- Serialization --

    def to_dict(self) -> dict[str, Any]:
        session_metrics = self._compute_session_metrics()
        return {
            "schema_version": SCHEMA_VERSION,
            "session_id": self.session_id,
            "started_at": self.started_at,
            "agent": {
                "name": "ming",
                "version": self.agent_version,
                "model": self.model,
            },
            "turns": [self._turn_to_dict(t) for t in self.turns],
            "compaction_events": [asdict(e) for e in self.compaction_events],
            "session_metrics": asdict(session_metrics),
        }

    def save(self, root: str | Path | None = None) -> Path:
        trace_root = Path(root) if root else Path.cwd() / ".ming" / "session_traces"
        trace_root.mkdir(parents=True, exist_ok=True)
        path = trace_root / f"{self.session_id}.json"
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Session trace saved: %s", path)
        return path

    def _compute_session_metrics(self) -> SessionMetrics:
        elapsed = time.monotonic() - self._session_start_time
        return SessionMetrics(
            total_turns=len(self.turns),
            adversarial_turns=sum(1 for t in self.turns if t.execution == "adversarial"),
            single_turns=sum(1 for t in self.turns if t.execution == "single"),
            error_turns=sum(1 for t in self.turns if t.error),
            compaction_count=len(self.compaction_events),
            total_llm_calls=sum(t.turn_metrics.total_llm_calls for t in self.turns),
            total_prompt_tokens=sum(t.turn_metrics.total_prompt_tokens for t in self.turns),
            total_completion_tokens=sum(t.turn_metrics.total_completion_tokens for t in self.turns),
            total_cost_usd=sum(t.turn_metrics.estimated_cost_usd for t in self.turns),
            wall_clock_seconds=round(elapsed, 1),
        )

    @staticmethod
    def _turn_to_dict(turn: TurnTrace) -> dict[str, Any]:
        d: dict[str, Any] = {
            "turn_id": turn.turn_id,
            "timestamp": turn.timestamp,
            "user_input": turn.user_input,
            "gate": asdict(turn.gate),
            "execution": turn.execution,
            "final_output": turn.final_output,
            "final_output_length": turn.final_output_length,
            "feedback": asdict(turn.feedback),
            "turn_metrics": asdict(turn.turn_metrics),
        }
        if turn.single_agent is not None:
            d["single_agent"] = asdict(turn.single_agent)
        if turn.adversarial is not None:
            d["adversarial"] = asdict(turn.adversarial)
        if turn.error:
            d["error"] = turn.error
        return d
