"""Core agent — integrates all subsystems.

Full pipeline per user turn:
  1. Context assembly (four-layer model)
  2. Cognitive routing (7 rules → single or adversarial)
  3a. Single mode: α_LOOP (reason → tool → loop)
  3b. Adversarial mode: Fork α/β → γ convergence
  4. Feedback: update Automaticity + memory
"""

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from ming.config import MingConfig, load_config
from ming.context.manager import ContextManager
from ming.core.adversarial import AdversarialResult, run_adversarial
from ming.core.automaticity import AutomaticityStore
from ming.core.cognitive_router import CognitiveRouter
from ming.core.llm import LLMResponse, Message, call_llm
from ming.core.loop_detection import LoopDetector
from ming.core.notepad import NotepadStore
from ming.core.permission import PermissionGate
from ming.core.progress import ProgressTracker, ToolEvent
from ming.core.recovery import FileSnapshotStore, format_llm_failure, format_tool_stall
from ming.core.session_trace import (
    AdversarialAgentTrace,
    CompactionEvent,
    LLMCallMetrics,
    SessionTrace,
    ToolCallTrace,
)
from ming.core.todo import TodoState
from ming.core.tool_selection import ToolSelector
from ming.core.trace import CheckpointStore, new_turn_id
from ming.memory.experience import ExperienceStore
from ming.memory.store import MemoryStore
from ming.tools.base import ToolRegistry, ToolResult
from ming.tools.bash import BashTool
from ming.tools.file import FileEditTool, FileReadTool, FileWriteTool
from ming.tools.web import WebFetchTool, WebResearchTool, WebSearchTool

logger = logging.getLogger("ming")


@dataclass(frozen=True)
class AgentProgressEvent:
    """High-signal progress event for user-facing agent-loop display."""

    stage: str
    message: str
    detail: str = ""
    turn_id: str = ""

SYSTEM_PROMPT = """\
你是 Ming（明），一个增强人类 System 2 思维的 AI 助手。
你的名字来自《道德经》：「知常曰明，不知常，妄作凶」。
你的核心价值是帮助人类看清模式、避免妄作。

你有工具可以使用：执行命令(bash)、读文件(file_read)、写文件(file_write)、编辑文件(file_edit)。
需要时主动使用工具完成任务，不要只用语言描述步骤。
完成后简洁汇报结果。"""

# T2 bias checklist — embedded in system prompt (always on)
T2_BIAS_CHECKLIST = """
[T2 偏差清单 — 每次决策前对照]
- 你是否在为自己已有的结论找理由（确认偏差）？
- 有没有你没考虑过的替代方案？
- 这个操作可以撤销吗？不可逆操作需要更谨慎。
- 你是否假设了某个前提但没有验证？
- 信息是否充分，还是需要先查证？"""


def _build_tool_registry(working_dir: str | None = None) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(BashTool(working_dir))
    registry.register(FileReadTool(working_dir))
    registry.register(FileWriteTool(working_dir))
    registry.register(FileEditTool(working_dir))
    registry.register(WebSearchTool())
    registry.register(WebFetchTool())
    registry.register(WebResearchTool())
    return registry


class Agent:
    """Ming agent — full pipeline."""

    def __init__(
        self,
        config: MingConfig | None = None,
        working_dir: str | None = None,
        progress_callback: Callable[[AgentProgressEvent], None] | None = None,
    ):
        self.config = config or load_config()
        self.working_dir = working_dir
        self.workspace_root = Path(working_dir) if working_dir else Path.cwd()
        self.progress_callback = progress_callback
        self.tools = _build_tool_registry(working_dir)

        # Subsystems
        self.context = ContextManager(
            max_context_tokens=self.config.context.max_context_tokens,
            compaction_threshold=self.config.context.compaction_threshold,
            safety_threshold=self.config.context.compaction_safety_threshold,
        )
        self.cognitive_router = CognitiveRouter()
        self.gate = self.cognitive_router  # compatibility for older integrations
        self.automaticity = AutomaticityStore()
        self.memory = MemoryStore()
        self.experience = ExperienceStore()
        self.loop_detector = LoopDetector()
        self.progress_tracker = ProgressTracker()
        self.permission_gate = PermissionGate()
        self.tool_selector = ToolSelector()
        self.notepad = NotepadStore(self.workspace_root / ".ming" / "scratch")
        self.checkpoints = CheckpointStore(self.workspace_root / ".ming" / "checkpoints")
        self.snapshots = FileSnapshotStore(self.workspace_root / ".ming" / "snapshots")
        self.session_trace = SessionTrace(
            model=self.config.llm.model,
            agent_version=self._get_version(),
        )
        self.last_checkpoint_path: Path | None = None
        self.current_turn_id = ""
        self.active_context_scopes = ["user", "project", "global"]
        self._last_t3_result = ""

        # Initialize context layers
        full_system = SYSTEM_PROMPT + "\n" + T2_BIAS_CHECKLIST
        self.context.set_base(full_system)

        # Load memory into session layer
        self.set_context_scopes(self.active_context_scopes)

    @property
    def messages(self) -> list[Message]:
        """Expose messages for CLI status/compatibility."""
        return self.context.get_messages()

    async def chat(self, user_input: str, turn_id: str | None = None) -> str:
        """Process user input through the full pipeline."""
        logger.debug(f"User input: {user_input[:100]}...")
        self.current_turn_id = turn_id or new_turn_id()
        self.session_trace.begin_turn(self.current_turn_id, user_input)
        self._emit_progress("context", "准备上下文")
        todo = TodoState.from_user_input(user_input)
        notepad_path = self.notepad.create(self.current_turn_id, user_input)
        self.notepad.add_assumption(notepad_path, "本轮只把高信号工作台信息注入上下文。")
        self.context.set_instant_context(self._build_instant_context(user_input))
        self.context.set_turn_workbench(todo=todo.to_context(), notepad_path=notepad_path)

        # Add user message to dialog layer
        self.context.add_message(Message(role="user", content=user_input))

        # Check compaction before proceeding
        if self.context.needs_compaction():
            logger.info("Context approaching limit, running compaction...")
            await self._run_compaction(trigger="threshold")

        # Cognitive routing
        automaticity = self.automaticity.get_automaticity(user_input)
        logger.debug(f"Automaticity for input: {automaticity:.2f}")
        context_tokens = self.context.current_tokens()
        routing_decision = self.cognitive_router.evaluate(
            user_input=user_input,
            context_tokens=context_tokens,
            automaticity=automaticity,
            has_historical_divergence=self.experience.has_historical_divergence(user_input),
        )
        logger.info(f"CognitiveRouter decision: {routing_decision}")
        route_message = "进入对抗分析" if routing_decision.is_adversarial else "使用单核执行"
        self._emit_progress("route", route_message, detail=str(routing_decision.triggered_rules))

        self.session_trace.record_gate(
            mode=routing_decision.mode,
            triggered_rules=routing_decision.triggered_rules,
            all_rules=routing_decision.all_rules_evaluated,
            automaticity=automaticity,
            context_tokens=context_tokens,
        )

        # Route based on cognitive router decision
        if routing_decision.is_adversarial:
            logger.info(f"Entering adversarial mode (rules: {routing_decision.triggered_rules})")
            try:
                result = await self._run_adversarial(user_input)
            except Exception as e:
                logger.error(f"Adversarial mode failed: {e}", exc_info=True)
                logger.info("Falling back to single-agent mode (L3 recovery)")
                return await self._run_single(user_input, todo=todo, notepad_path=notepad_path)
            # Record adversarial trace
            adv_trace = AdversarialAgentTrace(
                alpha_output_length=len(result.alpha_output),
                alpha_metrics=LLMCallMetrics.from_usage(
                    result.metrics.alpha_usage, result.metrics.alpha_latency_ms
                ),
                beta_output_length=len(result.beta_output),
                beta_metrics=LLMCallMetrics.from_usage(
                    result.metrics.beta_usage, result.metrics.beta_latency_ms
                ),
                gamma_phase1_consistency=result.consistency,
                gamma_phase1_metrics=LLMCallMetrics.from_usage(
                    result.metrics.gamma_phase1_usage, result.metrics.gamma_phase1_latency_ms
                ),
                gamma_phase2_ran=result.metrics.gamma_phase2_ran,
                gamma_phase2_metrics=LLMCallMetrics.from_usage(
                    result.metrics.gamma_phase2_usage, result.metrics.gamma_phase2_latency_ms
                ),
                tier_signal=result.tier_signal,
                total_latency_ms=result.metrics.total_latency_ms,
            )
            self.session_trace.record_adversarial(adv_trace)
            # Aggregate LLM call metrics for the turn
            for usage, latency in [
                (result.metrics.alpha_usage, result.metrics.alpha_latency_ms),
                (result.metrics.beta_usage, result.metrics.beta_latency_ms),
                (result.metrics.gamma_phase1_usage, result.metrics.gamma_phase1_latency_ms),
            ]:
                self.session_trace.record_llm_call(usage, latency)
            if result.metrics.gamma_phase2_ran:
                self.session_trace.record_llm_call(
                    result.metrics.gamma_phase2_usage, result.metrics.gamma_phase2_latency_ms
                )

            # Update automaticity with tier signal
            auto_before = automaticity
            self.automaticity.update(user_input, result.tier_signal)
            auto_after = self.automaticity.get_automaticity(user_input)
            self.experience.record(user_input, result.tier_signal, "adversarial")
            self.session_trace.record_feedback(auto_before, auto_after, result.tier_signal)

            self.context.add_message(Message(role="assistant", content=result.final_output))
            logger.info(
                "Adversarial result: consistency=%s, tier=%s",
                result.consistency,
                result.tier_signal,
            )
            return self._finish_turn(result.final_output, todo, notepad_path)
        else:
            return await self._run_single(user_input, todo=todo, notepad_path=notepad_path)

    async def _run_single(
        self,
        user_input: str,
        todo: TodoState | None = None,
        notepad_path: Path | None = None,
    ) -> str:
        """Run single-agent mode (α_LOOP)."""
        if todo is None:
            todo = TodoState.from_user_input(user_input)
        if notepad_path is None:
            notepad_path = self.notepad.create(self.current_turn_id, user_input)

        self.session_trace.init_single_path()

        iteration = 0
        turn_start = time.time()
        used_tools = False
        t3_repair_attempted = False
        tool_strategy_replan_attempted = False
        self.loop_detector.reset()
        self.progress_tracker.reset()
        selected_tool_names = self.tool_selector.select_tool_names(user_input, self.tools.names())
        selected_tool_schemas = self.tools.schemas_for(selected_tool_names)
        self.context.set_turn_workbench(
            todo=todo.to_context(),
            notepad_path=notepad_path,
            tool_names=selected_tool_names,
        )

        while True:
            iteration += 1

            # L5 ceiling: iteration limit
            if iteration > self.config.agent.max_iterations:
                self.session_trace.record_l5_ceiling("iteration_limit")
                msg = f"[Ming: 达到迭代上限 {self.config.agent.max_iterations}，停止执行]"
                self.context.add_message(Message(role="assistant", content=msg))
                return self._finish_turn(msg, todo, notepad_path)

            # L5 ceiling: wall-clock timeout
            elapsed = time.time() - turn_start
            if self.config.agent.max_seconds > 0 and elapsed > self.config.agent.max_seconds:
                self.session_trace.record_l5_ceiling("timeout")
                msg = f"[Ming: 超时 {self.config.agent.max_seconds}s，停止执行]"
                self.context.add_message(Message(role="assistant", content=msg))
                return self._finish_turn(msg, todo, notepad_path)

            # Safety compaction check mid-loop
            if self.context.needs_safety_compaction():
                logger.warning("Safety compaction triggered mid-loop")
                await self._run_compaction(trigger="safety")

            # Call LLM
            self._emit_progress("llm", f"调用模型，第 {iteration} 轮")
            self.session_trace.begin_step(iteration)
            llm_t0 = time.monotonic()
            try:
                response: LLMResponse = await call_llm(
                    messages=self.context.get_messages(),
                    config=self.config.llm,
                    tools=selected_tool_schemas or None,
                )
            except asyncio.CancelledError:
                msg = "[Ming: 已停止本轮思考]"
                self._emit_progress("cancelled", "已停止本轮思考")
                self.notepad.add_blocker(notepad_path, "用户停止了当前 agent-loop。")
                self.context.add_message(Message(role="assistant", content=msg))
                return self._finish_turn(msg, todo, notepad_path, complete_todo=False)
            except Exception as exc:
                logger.error("LLM call failed", exc_info=True)
                failure = format_llm_failure(exc)
                msg = failure.user_message
                self._emit_progress("error", "模型调用失败", detail=failure.user_message)
                self.notepad.add_blocker(notepad_path, f"llm_error: {failure.technical_detail}")
                self.context.add_message(Message(role="assistant", content=msg))
                return self._finish_turn(msg, todo, notepad_path, complete_todo=False)

            llm_latency_ms = int((time.monotonic() - llm_t0) * 1000)
            step_metrics = LLMCallMetrics.from_usage(response.usage, llm_latency_ms)
            self.session_trace.record_llm_call(response.usage, llm_latency_ms)

            # Case 1: Tool calls → execute and loop
            if response.tool_calls:
                used_tools = True
                replan_requested = False
                step_tool_traces: list[ToolCallTrace] = []
                self.context.add_message(Message(
                    role="assistant",
                    content=response.content,
                    tool_calls=response.tool_calls,
                ))

                for tc in response.tool_calls:
                    func = tc["function"]
                    tool_name = func["name"]
                    tool_args = func["arguments"]
                    self._emit_progress("tool", f"执行工具 {tool_name}", detail=tool_args)

                    # Loop detection (L5 fingerprint layer)
                    loop_status = self.loop_detector.check(tool_name, tool_args)

                    if loop_status == "block":
                        result = ToolResult(
                            output=f"[Loop detected] Tool {tool_name} has been called identically "
                                   f"{self.loop_detector._consecutive_identical} times. "
                                   "You must try a completely different approach.",
                            is_error=True,
                        )
                    elif loop_status == "warn":
                        # Execute but inject warning
                        result = await self._execute_permitted_tool(tool_name, tool_args)
                        result = ToolResult(
                            output=result.output + "\n\n[Warning: You've called this tool with "
                            "identical parameters multiple times. Consider a different approach.]",
                            is_error=result.is_error,
                        )
                    else:
                        # L1: Normal execution (harness handles transient retries via LiteLLM)
                        result = await self._execute_permitted_tool(tool_name, tool_args)

                    tool_latency_ms = int((time.monotonic() - llm_t0) * 1000) - llm_latency_ms
                    step_tool_traces.append(ToolCallTrace(
                        id=tc["id"],
                        name=tool_name,
                        arguments=tool_args,
                        loop_status=loop_status,
                        consecutive_identical=self.loop_detector._consecutive_identical,
                        result_output_length=len(result.output),
                        result_is_error=result.is_error,
                        latency_ms=max(0, tool_latency_ms),
                    ))

                    logger.info(
                        "Tool: %s → %s (%s chars)",
                        tool_name,
                        "ERROR" if result.is_error else "OK",
                        len(result.output),
                    )

                    event = ToolEvent.from_tool_result(
                        tool_name=tool_name,
                        tool_args=tool_args,
                        output=result.output,
                        is_error=result.is_error,
                    )
                    assessment = self.progress_tracker.record(event)
                    self.notepad.add_tool_observation(
                        notepad_path,
                        f"tool={tool_name} status={event.status} progress={event.progress}",
                    )
                    if event.status == "error":
                        self.notepad.add_blocker(
                            notepad_path,
                            f"{tool_name}: {result.output[:300]}",
                        )
                    elif event.evidence_count > 0:
                        evidence = f"{tool_name}: {result.output[:500]}"
                        self.notepad.add_evidence(notepad_path, tool_name, result.output[:500])
                        self.context.pin_evidence(evidence)
                    todo.mark_step_completed(tool_name)
                    self.context.set_turn_workbench(
                        todo=todo.to_context(),
                        notepad_path=notepad_path,
                        tool_names=selected_tool_names,
                    )

                    self.context.add_message(Message(
                        role="tool",
                        content=result.output,
                        tool_call_id=tc["id"],
                    ))

                    if assessment.decision == "stop":
                        failure = format_tool_stall(assessment, self.progress_tracker.events)
                        msg = failure.user_message
                        self.notepad.add_blocker(
                            notepad_path,
                            f"tool_stall: {failure.technical_detail}",
                        )
                        self.context.add_message(Message(role="assistant", content=msg))
                        return self._finish_turn(msg, todo, notepad_path)

                    if (
                        assessment.decision == "replan"
                        and not tool_strategy_replan_attempted
                    ):
                        tool_strategy_replan_attempted = True
                        replan_prompt = self._build_tool_strategy_replan_prompt(
                            assessment,
                            self.progress_tracker.events[-3:],
                        )
                        self._emit_progress(
                            "route",
                            "工具调用策略失败，切换执行方式",
                            detail=assessment.reason,
                        )
                        self.notepad.add_blocker(notepad_path, f"tool_replan: {assessment.reason}")
                        self.context.add_message(Message(role="user", content=replan_prompt))
                        replan_requested = True
                        break

                self.session_trace.finish_step(
                    iteration=iteration,
                    response_content_length=len(response.content),
                    tool_calls=step_tool_traces,
                    is_final=False,
                    metrics=step_metrics,
                )

                if replan_requested:
                    continue

                continue

            # Case 2: Done (no tool calls)
            self.session_trace.finish_step(
                iteration=iteration,
                response_content_length=len(response.content),
                tool_calls=[],
                is_final=True,
                metrics=step_metrics,
            )
            final_content = self._strip_final_marker(response.content)

            if not used_tools:
                self._emit_progress("verify", "执行 T1 自检")
                t1_t0 = time.monotonic()
                final_content, tier_signal = await self._run_t1_self_check(final_content)
                t1_latency = int((time.monotonic() - t1_t0) * 1000)
                self.session_trace.record_t1_check(
                    draft_changed=(tier_signal == "T1_caught"),
                    tier_signal=tier_signal,
                    metrics=LLMCallMetrics(latency_ms=t1_latency),
                )
            else:
                self._emit_progress("verify", "执行 T3 核验")
                t3_t0 = time.monotonic()
                tier_signal = await self._run_t3_fact_check(user_input, final_content)
                t3_latency = int((time.monotonic() - t3_t0) * 1000)
                self.session_trace.record_t3_check(
                    passed=(tier_signal == "T3_pass"),
                    tier_signal=tier_signal,
                    repair_attempted=t3_repair_attempted,
                    metrics=LLMCallMetrics(latency_ms=t3_latency),
                )
                if tier_signal == "T3_error" and not t3_repair_attempted:
                    t3_repair_attempted = True
                    self.context.add_message(
                        Message(
                            role="user",
                            content=(
                                "T3 核验失败：最终答复与工具证据不一致。"
                                "请基于工具结果修正方案，必要时重新调用工具。"
                            ),
                        )
                    )
                    continue

            self.context.add_message(Message(role="assistant", content=final_content))
            self._maybe_encode_explicit_memory(user_input)

            auto_before = self.automaticity.get_automaticity(user_input)
            self.automaticity.update(user_input, tier_signal)
            auto_after = self.automaticity.get_automaticity(user_input)
            self.experience.record(user_input, tier_signal, "single")
            self.session_trace.record_feedback(auto_before, auto_after, tier_signal)

            return self._finish_turn(final_content, todo, notepad_path)

    async def _execute_permitted_tool(self, tool_name: str, tool_args: str) -> ToolResult:
        decision = self.permission_gate.evaluate(tool_name, tool_args)
        if not decision.allowed:
            return ToolResult(output=f"[Permission denied] {decision.reason}", is_error=True)
        if tool_name in {"file_write", "file_edit"}:
            self._snapshot_file_tool_target(tool_args)
        return await self.tools.execute(tool_name, tool_args)

    def _snapshot_file_tool_target(self, tool_args: str) -> None:
        try:
            data = json.loads(tool_args) if tool_args else {}
        except json.JSONDecodeError:
            return
        raw_path = data.get("path")
        if not raw_path:
            return
        path = Path(raw_path)
        target = path if path.is_absolute() else self.workspace_root / path
        self.snapshots.snapshot(target)

    def rollback_last_change(self) -> dict[str, int | str]:
        """Roll back the most recent file_write/file_edit snapshot."""
        return self.snapshots.rollback_latest()

    def resume_latest_checkpoint(self, checkpoint_id: str = "latest") -> dict | None:
        """Restore dialog context from a checkpoint."""
        path = self.checkpoints.resolve(checkpoint_id)
        if path is None:
            return None
        payload = self.checkpoints.load(path)
        restored = [Message(**message) for message in payload.get("messages", [])]
        base_content = {message.content for message in self.context.base_layer}
        self.context.dialog_history = [
            message
            for message in restored
            if not (message.role == "system" and message.content in base_content)
        ]
        self.last_checkpoint_path = path
        return payload

    def cleanup_runtime(self, keep_checkpoints: int = 20) -> dict[str, int]:
        return {"checkpoints_removed": self.checkpoints.cleanup(keep=keep_checkpoints)}

    def _finish_turn(
        self,
        final_output: str,
        todo: TodoState,
        notepad_path: Path,
        complete_todo: bool = True,
    ) -> str:
        if complete_todo:
            todo.complete_all()
        checkpoint_path = self.checkpoints.save(
            self.current_turn_id,
            self.context.get_messages(),
            notepad_path,
            todo,
        )
        self.last_checkpoint_path = checkpoint_path

        self.session_trace.finish_turn(final_output)

        self._emit_progress(
            "done",
            "完成本轮响应",
            detail=f"checkpoint={checkpoint_path}",
        )
        return final_output

    def _emit_progress(self, stage: str, message: str, detail: str = "") -> None:
        if not self.progress_callback:
            return
        try:
            self.progress_callback(
                AgentProgressEvent(
                    stage=stage,
                    message=message,
                    detail=detail,
                    turn_id=self.current_turn_id,
                )
            )
        except Exception as exc:
            logger.debug("Progress callback failed: %s", exc)

    def _build_instant_context(self, user_input: str) -> str:
        return (
            f"当前用户请求：{user_input}\n"
            "只使用和本轮任务相关的工具、记忆、TODO、Notepad 和 pinned evidence。"
        )

    def _build_tool_strategy_replan_prompt(
        self,
        assessment,
        recent_events: list[ToolEvent],
    ) -> str:
        diagnostics = "; ".join(
            f"{event.tool_name}:{event.progress}:{event.diagnostic[:120]}"
            for event in recent_events
            if event.diagnostic
        )
        return (
            "工具调用策略失败，需要换一种执行方式继续，不要把问题交给用户。\n"
            f"原因：{assessment.reason}\n"
            f"最近诊断：{diagnostics or '无详细诊断'}\n"
            "请立即调整策略：不要继续用损坏的 JSON；不要用 bash 嵌入大段多行内容。"
            "写文件优先使用 file_write 的合法 JSON 参数；修改已有文件优先用 file_edit；"
            "如果内容很长，缩小到必要改动或分块写入。"
        )

    async def _run_adversarial(self, user_input: str) -> AdversarialResult:
        """Run adversarial mode (α/β Fork + γ convergence)."""
        logger.info("Running adversarial mode")
        return await run_adversarial(
            dialog_history=self.context.get_messages(),
            config=self.config.llm,
        )

    async def _run_compaction(self, trigger: str = "manual") -> None:
        """Run context compaction with LLM summarization."""
        tokens_before = self.context.current_tokens()
        messages_before = len(self.context.dialog_history)

        async def _compact_llm_call(messages, config=None):
            return await call_llm(
                messages=messages,
                config=config or self.config.llm,
            )

        compact_t0 = time.monotonic()
        await self.context.compact(_compact_llm_call)
        compact_latency = int((time.monotonic() - compact_t0) * 1000)

        tokens_after = self.context.current_tokens()
        messages_after = len(self.context.dialog_history)
        ratio = round(tokens_after / max(tokens_before, 1), 2)

        self.session_trace.record_compaction(CompactionEvent(
            timestamp=datetime.now().isoformat(timespec="seconds"),
            trigger=trigger,
            before_tokens=tokens_before,
            after_tokens=tokens_after,
            compression_ratio=ratio,
            messages_before=messages_before,
            messages_after=messages_after,
            phase1_messages_pruned=max(0, messages_before - messages_after),
            phase2_ran=True,
            phase2_metrics=LLMCallMetrics(latency_ms=compact_latency),
        ))

    async def compact_now(self) -> None:
        """Public CLI hook for manual compaction."""
        await self._run_compaction(trigger="manual")

    def clear_dialog(self) -> int:
        """Clear current dialog while preserving loaded memory/session context."""
        return self.context.clear_dialog()

    def forget_scope(self, scope: str) -> dict[str, int]:
        """Forget a scoped set of state without conflating dialog and memory."""
        normalized = scope.strip().lower()
        if normalized == "session":
            return {"session_context_removed": self.context.clear_session_context()}
        if normalized in {"memory", "memories", "user"}:
            return {"memory_removed": self.memory.delete_by_type("user")}
        if normalized == "project":
            return {"memory_removed": self.memory.delete_by_type("project")}
        raise ValueError("Unknown forget scope. Use: session, memory, project.")

    def set_context_scopes(self, scopes: list[str]) -> dict[str, list[str] | int]:
        """Switch active memory scopes injected into session context."""
        allowed = {"user", "project", "global"}
        normalized = [scope.strip().lower() for scope in scopes if scope.strip()]
        invalid = [scope for scope in normalized if scope not in allowed]
        if invalid:
            raise ValueError("Unknown context scope. Use: user, project, global.")
        if not normalized:
            normalized = ["user", "project", "global"]
        self.active_context_scopes = normalized
        mem_context = self.memory.get_scoped_context(normalized)
        removed = self.context.replace_session_context(mem_context, label="memories")
        return {"active_scopes": normalized, "session_context_replaced": removed}

    def rewind_last_turn(self) -> int:
        """Remove the most recent user turn and all messages after it."""
        for index in range(len(self.context.dialog_history) - 1, -1, -1):
            if self.context.dialog_history[index].role == "user":
                removed = len(self.context.dialog_history) - index
                del self.context.dialog_history[index:]
                return removed
        return 0

    async def _run_t1_self_check(self, draft: str) -> tuple[str, str]:
        """Run a lightweight same-session CoVe-style self check before output."""
        check_messages = self.context.get_messages() + [
            Message(role="assistant", content=draft),
            Message(
                role="user",
                content=(
                    "T1 CoVe 自检：请检查上一条草稿是否有明显事实错误、遗漏或自相矛盾。"
                    "如果草稿可直接发给用户，回复 `FINAL: <原文>`；"
                    "如果需要修正，回复 `FINAL: <修正后正文>`。"
                ),
            ),
        ]
        try:
            response = await call_llm(messages=check_messages, config=self.config.llm)
        except Exception as exc:
            logger.warning(f"T1 self-check failed, using draft output: {exc}")
            return draft, "T0_success"

        checked = self._strip_final_marker(response.content)
        if not checked:
            return draft, "T0_success"
        if checked.strip() != draft.strip():
            return checked, "T1_caught"
        return checked, "T0_success"

    async def _run_t3_fact_check(self, user_input: str, final_content: str) -> str:
        """Run a fresh-context fact check after tool-backed artifact work."""
        recent_tool_outputs = [
            msg.content for msg in self.context.dialog_history[-12:] if msg.role == "tool"
        ]
        fact_messages = [
            Message(
                role="system",
                content=(
                    "T3 事实核验子：你只做 fresh-context 核验。"
                    "检查工具结果是否支持最终答复。"
                    "若一致，回复 `PASS: <一句话理由>`；若不一致，回复 `FAIL: <问题>`。"
                ),
            ),
            Message(
                role="user",
                content=(
                    f"用户请求：\n{user_input}\n\n"
                    f"工具结果：\n{chr(10).join(recent_tool_outputs[-6:])}\n\n"
                    f"最终答复草稿：\n{final_content}"
                ),
            ),
        ]
        try:
            response = await call_llm(messages=fact_messages, config=self.config.llm)
        except Exception as exc:
            logger.warning(f"T3 fact-check failed: {exc}")
            return "T3_error"

        self._last_t3_result = response.content
        if response.content.strip().lower().startswith("fail"):
            warning = f"T3 fact-check flagged output: {response.content}"
            logger.warning(warning)
            return "T3_error"
        return "T3_pass"

    def _maybe_encode_explicit_memory(self, user_input: str) -> None:
        """Persist explicit user memory requests."""
        if not re.search(r"\bremember\b|记住|请记住", user_input, flags=re.IGNORECASE):
            return

        content = re.sub(r"^(请)?记住[:：\s]*", "", user_input).strip()
        if not content:
            content = user_input.strip()

        name = f"explicit_memory_{datetime.now():%Y%m%d_%H%M%S}"
        self.memory.save(
            name=name,
            description=content[:80],
            mem_type="user",
            content=content,
        )

    @staticmethod
    def _strip_final_marker(content: str) -> str:
        stripped = content.strip()
        if stripped.upper().startswith("FINAL:"):
            return stripped.split(":", 1)[1].strip()
        if stripped.startswith("最终："):
            return stripped.split("：", 1)[1].strip()
        return content

    def save_session_trace(self) -> Path:
        """Save the accumulated session trace to disk."""
        return self.session_trace.save(self.workspace_root / ".ming" / "session_traces")

    @staticmethod
    def _get_version() -> str:
        try:
            from ming import __version__
            return __version__
        except Exception:
            return "unknown"

    def chat_sync(self, user_input: str) -> str:
        """Synchronous wrapper."""
        return asyncio.run(self.chat(user_input))
