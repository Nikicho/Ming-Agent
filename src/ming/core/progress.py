"""Tool event recording and lightweight progress assessment."""

import json
from dataclasses import dataclass


@dataclass
class ToolEvent:
    tool_name: str
    action: str
    status: str
    output_chars: int
    evidence_count: int
    progress: str
    diagnostic: str = ""

    @classmethod
    def from_tool_result(
        cls,
        tool_name: str,
        tool_args: str,
        output: str,
        is_error: bool,
    ) -> "ToolEvent":
        action = _classify_action(tool_name, tool_args)
        output_chars = len(output)

        if is_error:
            return cls(
                tool_name,
                action,
                "error",
                output_chars,
                0,
                _classify_error_progress(tool_name, tool_args, output),
                diagnostic=output[:500],
            )

        evidence_count = _count_evidence(output)
        if tool_name in {"file_write", "file_edit"}:
            progress = "new_evidence"
        elif tool_name == "bash" and output_chars > 0:
            progress = "new_evidence"
        elif tool_name == "file_read" and output_chars > 0:
            progress = "new_evidence"
        elif evidence_count > 0:
            progress = "new_evidence"
        elif tool_name in {"web_search", "web_fetch"}:
            progress = "unknown" if output_chars > 100 else "artifact_noise"
        elif output_chars > 20:
            progress = "unknown"
        else:
            progress = "no_signal"

        return cls(tool_name, action, "ok", output_chars, evidence_count, progress)


@dataclass
class ProgressAssessment:
    decision: str  # continue, replan, nudge, nudge_strong
    reason: str


class ProgressTracker:
    def __init__(self, max_no_signal_streak: int = 5, strong_nudge_threshold: int = 8):
        self.max_no_signal_streak = max_no_signal_streak
        self.strong_nudge_threshold = strong_nudge_threshold
        self.no_signal_streak = 0
        self.tool_strategy_failure_streak = 0
        self._strategy_replan_emitted = False
        self._nudge_streak_emitted = 0
        self.events: list[ToolEvent] = []

    def reset(self) -> None:
        self.no_signal_streak = 0
        self.tool_strategy_failure_streak = 0
        self._strategy_replan_emitted = False
        self._nudge_streak_emitted = 0
        self.events = []

    def record(self, event: ToolEvent) -> ProgressAssessment:
        self.events.append(event)

        if event.progress in {
            "no_signal",
            "artifact_noise",
            "tool_input_error",
            "tool_strategy_error",
        }:
            self.no_signal_streak += 1
        else:
            self.no_signal_streak = 0

        if event.progress in {"tool_input_error", "tool_strategy_error"}:
            self.tool_strategy_failure_streak += 1
        else:
            self.tool_strategy_failure_streak = 0

        if (
            self.tool_strategy_failure_streak >= 2
            and not self._strategy_replan_emitted
        ):
            self._strategy_replan_emitted = True
            return ProgressAssessment(
                decision="replan",
                reason=(
                    "工具调用策略失败：连续出现工具参数格式错误或不可靠的写入策略，"
                    "需要改用更稳的工具调用方式。"
                ),
            )

        if (
            self.no_signal_streak >= self.strong_nudge_threshold
            and self._nudge_streak_emitted < self.strong_nudge_threshold
        ):
            self._nudge_streak_emitted = self.no_signal_streak
            return ProgressAssessment(
                decision="nudge_strong",
                reason=(
                    f"连续 {self.no_signal_streak} 次工具调用进展有限，"
                    "强烈建议换一种方法、缩小目标，或向用户确认方向。"
                ),
            )

        if (
            self.no_signal_streak >= self.max_no_signal_streak
            and self._nudge_streak_emitted < self.max_no_signal_streak
        ):
            self._nudge_streak_emitted = self.no_signal_streak
            return ProgressAssessment(
                decision="nudge",
                reason=(
                    f"连续 {self.no_signal_streak} 次工具调用进展有限，"
                    "考虑是否需要调整策略。"
                ),
            )

        return ProgressAssessment(decision="continue", reason="progress acceptable")


def _classify_action(tool_name: str, tool_args: str) -> str:
    if tool_name in {"web_search", "web_fetch"}:
        return tool_name
    if tool_name == "bash":
        lowered = tool_args.lower()
        if "curl" in lowered or "duckduckgo" in lowered or "bing.com/search" in lowered:
            return "shell_web_attempt"
    return tool_name


def _classify_error_progress(tool_name: str, tool_args: str, output: str) -> str:
    lowered_output = output.lower()
    if "invalid json arguments" in lowered_output or "unterminated string" in lowered_output:
        return "tool_input_error"
    if tool_name == "bash":
        if len(tool_args) > 2000 or "command line is too long" in lowered_output:
            return "tool_strategy_error"
        if "too long" in lowered_output:
            return "tool_strategy_error"
        if "命令行太长" in output:
            return "tool_strategy_error"
    return "no_signal"


def _count_evidence(output: str) -> int:
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return 0

    if isinstance(data, dict):
        results = data.get("results")
        if isinstance(results, list):
            return len(results)
        if data.get("text"):
            return 1
    return 0
