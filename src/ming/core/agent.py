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
import time
from typing import Any

from ming.config import MingConfig, load_config
from ming.context.manager import ContextManager
from ming.core.adversarial import AdversarialResult, run_adversarial
from ming.core.automaticity import AutomaticityStore
from ming.core.gate import Gate, GateDecision
from ming.core.llm import LLMResponse, Message, call_llm
from ming.core.loop_detection import LoopDetector
from ming.memory.store import MemoryStore
from ming.tools.base import ToolRegistry, ToolResult
from ming.tools.bash import BashTool
from ming.tools.file import FileEditTool, FileReadTool, FileWriteTool

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
    return registry


class Agent:
    """Ming agent — full pipeline."""

    def __init__(self, config: MingConfig | None = None, working_dir: str | None = None):
        self.config = config or load_config()
        self.working_dir = working_dir
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
        self.loop_detector = LoopDetector()

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
                return await self._run_single(user_input)
            # Update automaticity with tier signal
            self.automaticity.update(user_input, result.tier_signal)
            self.context.add_message(Message(role="assistant", content=result.final_output))
            logger.info(f"Adversarial result: consistency={result.consistency}, tier={result.tier_signal}")
            return result.final_output
        else:
            return await self._run_single(user_input)

    async def _run_single(self, user_input: str) -> str:
        """Run single-agent mode (α_LOOP)."""
        iteration = 0
        turn_start = time.time()
        self.loop_detector.reset()

        while True:
            iteration += 1

            # L5 ceiling: iteration limit
            if iteration > self.config.agent.max_iterations:
                msg = f"[Ming: 达到迭代上限 {self.config.agent.max_iterations}，停止执行]"
                self.context.add_message(Message(role="assistant", content=msg))
                return msg

            # L5 ceiling: wall-clock timeout
            elapsed = time.time() - turn_start
            if self.config.agent.max_seconds > 0 and elapsed > self.config.agent.max_seconds:
                msg = f"[Ming: 超时 {self.config.agent.max_seconds}s，停止执行]"
                self.context.add_message(Message(role="assistant", content=msg))
                return msg

            # Safety compaction check mid-loop
            if self.context.needs_safety_compaction():
                logger.warning("Safety compaction triggered mid-loop")
                await self._run_compaction()

            # Call LLM
            response: LLMResponse = await call_llm(
                messages=self.context.get_messages(),
                config=self.config.llm,
                tools=self.tools.all_schemas(),
            )

            # Case 1: Tool calls → execute and loop
            if response.tool_calls:
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
                        result = await self.tools.execute(tool_name, tool_args)
                        result = ToolResult(
                            output=result.output + "\n\n[Warning: You've called this tool with "
                                   "identical parameters multiple times. Consider a different approach.]",
                            is_error=result.is_error,
                        )
                    else:
                        # L1: Normal execution (harness handles transient retries via LiteLLM)
                        result = await self.tools.execute(tool_name, tool_args)

                    logger.info(f"Tool: {tool_name} → {'ERROR' if result.is_error else 'OK'} ({len(result.output)} chars)")

                    self.context.add_message(Message(
                        role="tool",
                        content=result.output,
                        tool_call_id=tc["id"],
                    ))

                continue

            # Case 2: Done (no tool calls)
            self.context.add_message(Message(role="assistant", content=response.content))

            # Update automaticity: no verification needed → T0 success
            self.automaticity.update(user_input, "T0_success")

            return response.content

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

    def chat_sync(self, user_input: str) -> str:
        """Synchronous wrapper."""
        return asyncio.run(self.chat(user_input))
