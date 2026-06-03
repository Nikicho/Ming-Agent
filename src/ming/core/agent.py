"""Core agent — integrates all subsystems.

Full pipeline per user turn:
  1. Context assembly (four-layer model)
  2. Gate evaluation (7 rules → single or adversarial)
  3a. Single mode: α_LOOP (reason → tool → loop)
  3b. Adversarial mode: Fork α/β → γ convergence
  4. Feedback: update Automaticity + memory
"""

import asyncio
import logging
import re
import time
from datetime import datetime
from pathlib import Path

from ming.config import MingConfig, load_config
from ming.context.manager import ContextManager
from ming.core.adversarial import AdversarialResult, run_adversarial
from ming.core.automaticity import AutomaticityStore
from ming.core.gate import Gate
from ming.core.llm import LLMResponse, Message, call_llm
from ming.core.loop_detection import LoopDetector
from ming.core.notepad import NotepadStore
from ming.core.permission import PermissionGate
from ming.core.progress import ProgressTracker, ToolEvent
from ming.core.todo import TodoState
from ming.core.tool_selection import ToolSelector
from ming.core.trace import CheckpointStore, RunTrace, new_turn_id
from ming.memory.experience import ExperienceStore
from ming.memory.store import MemoryStore
from ming.tools.base import ToolRegistry, ToolResult
from ming.tools.bash import BashTool
from ming.tools.file import FileEditTool, FileReadTool, FileWriteTool
from ming.tools.web import WebFetchTool, WebSearchTool

logger = logging.getLogger("ming")

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
    return registry


class Agent:
    """Ming agent — full pipeline."""

    def __init__(self, config: MingConfig | None = None, working_dir: str | None = None):
        self.config = config or load_config()
        self.working_dir = working_dir
        self.workspace_root = Path(working_dir) if working_dir else Path.cwd()
        self.tools = _build_tool_registry(working_dir)

        # Subsystems
        self.context = ContextManager(
            max_context_tokens=self.config.context.max_context_tokens,
            compaction_threshold=self.config.context.compaction_threshold,
            safety_threshold=self.config.context.compaction_safety_threshold,
        )
        self.gate = Gate()
        self.automaticity = AutomaticityStore()
        self.memory = MemoryStore()
        self.experience = ExperienceStore()
        self.loop_detector = LoopDetector()
        self.progress_tracker = ProgressTracker()
        self.permission_gate = PermissionGate()
        self.tool_selector = ToolSelector()
        self.notepad = NotepadStore(self.workspace_root / ".ming" / "scratch")
        self.checkpoints = CheckpointStore(self.workspace_root / ".ming" / "checkpoints")
        self.trace_root = self.workspace_root / ".ming" / "traces"
        self.last_trace_path: Path | None = None
        self.last_checkpoint_path: Path | None = None
        self._last_t3_result = ""

        # Initialize context layers
        full_system = SYSTEM_PROMPT + "\n" + T2_BIAS_CHECKLIST
        self.context.set_base(full_system)

        # Load memory into session layer
        mem_context = self.memory.get_session_context()
        if mem_context:
            self.context.add_session_context(mem_context, label="memories")

    @property
    def messages(self) -> list[Message]:
        """Expose messages for CLI status/compatibility."""
        return self.context.get_messages()

    async def chat(self, user_input: str) -> str:
        """Process user input through the full pipeline."""
        logger.debug(f"User input: {user_input[:100]}...")
        trace = RunTrace(new_turn_id(), user_input)
        todo = TodoState.from_user_input(user_input)
        notepad_path = self.notepad.create(trace.turn_id, user_input)

        # Add user message to dialog layer
        self.context.add_message(Message(role="user", content=user_input))

        # Check compaction before proceeding
        if self.context.needs_compaction():
            logger.info("Context approaching limit, running compaction...")
            await self._run_compaction()

        # Gate evaluation
        automaticity = self.automaticity.get_automaticity(user_input)
        logger.debug(f"Automaticity for input: {automaticity:.2f}")
        gate_decision = self.gate.evaluate(
            user_input=user_input,
            context_tokens=self.context.current_tokens(),
            automaticity=automaticity,
            has_historical_divergence=self.experience.has_historical_divergence(user_input),
        )
        logger.info(f"Gate decision: {gate_decision}")

        # Route based on gate decision
        if gate_decision.is_adversarial:
            logger.info(f"Entering adversarial mode (rules: {gate_decision.triggered_rules})")
            try:
                result = await self._run_adversarial(user_input)
            except Exception as e:
                logger.error(f"Adversarial mode failed: {e}", exc_info=True)
                logger.info("Falling back to single-agent mode (L3 recovery)")
                return await self._run_single(user_input, trace, todo, notepad_path)
            # Update automaticity with tier signal
            self.automaticity.update(user_input, result.tier_signal)
            self.experience.record(user_input, result.tier_signal, "adversarial")
            self.context.add_message(Message(role="assistant", content=result.final_output))
            logger.info(
                "Adversarial result: consistency=%s, tier=%s",
                result.consistency,
                result.tier_signal,
            )
            return self._finish_turn(result.final_output, trace, todo, notepad_path)
        else:
            return await self._run_single(user_input, trace, todo, notepad_path)

    async def _run_single(
        self,
        user_input: str,
        trace: RunTrace | None = None,
        todo: TodoState | None = None,
        notepad_path: Path | None = None,
    ) -> str:
        """Run single-agent mode (α_LOOP)."""
        if trace is None:
            trace = RunTrace(new_turn_id(), user_input)
        if todo is None:
            todo = TodoState.from_user_input(user_input)
        if notepad_path is None:
            notepad_path = self.notepad.create(trace.turn_id, user_input)

        iteration = 0
        turn_start = time.time()
        used_tools = False
        self.loop_detector.reset()
        self.progress_tracker.reset()
        selected_tool_names = self.tool_selector.select_tool_names(user_input, self.tools.names())
        selected_tool_schemas = self.tools.schemas_for(selected_tool_names)

        while True:
            iteration += 1

            # L5 ceiling: iteration limit
            if iteration > self.config.agent.max_iterations:
                msg = f"[Ming: 达到迭代上限 {self.config.agent.max_iterations}，停止执行]"
                self.context.add_message(Message(role="assistant", content=msg))
                return self._finish_turn(msg, trace, todo, notepad_path)

            # L5 ceiling: wall-clock timeout
            elapsed = time.time() - turn_start
            if self.config.agent.max_seconds > 0 and elapsed > self.config.agent.max_seconds:
                msg = f"[Ming: 超时 {self.config.agent.max_seconds}s，停止执行]"
                self.context.add_message(Message(role="assistant", content=msg))
                return self._finish_turn(msg, trace, todo, notepad_path)

            # Safety compaction check mid-loop
            if self.context.needs_safety_compaction():
                logger.warning("Safety compaction triggered mid-loop")
                await self._run_compaction()

            # Call LLM
            response: LLMResponse = await call_llm(
                messages=self.context.get_messages(),
                config=self.config.llm,
                tools=selected_tool_schemas or None,
            )

            # Case 1: Tool calls → execute and loop
            if response.tool_calls:
                used_tools = True
                self.context.add_message(Message(
                    role="assistant",
                    content=response.content,
                    tool_calls=response.tool_calls,
                ))

                for tc in response.tool_calls:
                    func = tc["function"]
                    tool_name = func["name"]
                    tool_args = func["arguments"]

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
                    trace.add_tool_event(event)
                    self.notepad.append(
                        notepad_path,
                        f"- tool={tool_name} status={event.status} progress={event.progress}",
                    )

                    self.context.add_message(Message(
                        role="tool",
                        content=result.output,
                        tool_call_id=tc["id"],
                    ))

                    if assessment.decision == "stop":
                        msg = (
                            "[Ming: 工具循环已停止]\n"
                            f"{assessment.reason}\n"
                            "请换用更可靠的工具、缩小目标，或让用户提供来源。"
                        )
                        self.context.add_message(Message(role="assistant", content=msg))
                        return self._finish_turn(msg, trace, todo, notepad_path)

                continue

            # Case 2: Done (no tool calls)
            final_content = self._strip_final_marker(response.content)

            if not used_tools:
                final_content, tier_signal = await self._run_t1_self_check(final_content)
            else:
                tier_signal = await self._run_t3_fact_check(user_input, final_content)

            self.context.add_message(Message(role="assistant", content=final_content))
            self._maybe_encode_explicit_memory(user_input)

            self.automaticity.update(user_input, tier_signal)
            self.experience.record(user_input, tier_signal, "single")

            return self._finish_turn(final_content, trace, todo, notepad_path)

    async def _execute_permitted_tool(self, tool_name: str, tool_args: str) -> ToolResult:
        decision = self.permission_gate.evaluate(tool_name, tool_args)
        if not decision.allowed:
            return ToolResult(output=f"[Permission denied] {decision.reason}", is_error=True)
        return await self.tools.execute(tool_name, tool_args)

    def _finish_turn(
        self,
        final_output: str,
        trace: RunTrace,
        todo: TodoState,
        notepad_path: Path,
    ) -> str:
        todo.complete_all()
        trace.final_output = final_output
        trace_path = trace.save(self.trace_root)
        checkpoint_path = self.checkpoints.save(
            trace.turn_id,
            self.context.get_messages(),
            trace_path,
            notepad_path,
            todo,
        )
        self.last_trace_path = trace_path
        self.last_checkpoint_path = checkpoint_path
        return final_output

    async def _run_adversarial(self, user_input: str) -> AdversarialResult:
        """Run adversarial mode (α/β Fork + γ convergence)."""
        logger.info("Running adversarial mode")
        return await run_adversarial(
            dialog_history=self.context.get_messages(),
            config=self.config.llm,
        )

    async def _run_compaction(self) -> None:
        """Run context compaction with LLM summarization."""
        async def _compact_llm_call(messages, config=None):
            return await call_llm(
                messages=messages,
                config=config or self.config.llm,
            )

        await self.context.compact(_compact_llm_call)

    async def compact_now(self) -> None:
        """Public CLI hook for manual compaction."""
        await self._run_compaction()

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

    def chat_sync(self, user_input: str) -> str:
        """Synchronous wrapper."""
        return asyncio.run(self.chat(user_input))
