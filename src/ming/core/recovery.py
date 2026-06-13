"""Local recovery helpers for file tool changes and user-facing failures."""

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from ming.core.progress import ProgressAssessment, ToolEvent


@dataclass
class FileSnapshot:
    snapshot_id: str
    path: str
    existed: bool
    content: str = ""


@dataclass
class ErrorAssessment:
    category: str
    retryable: bool
    recoverable: bool
    summary: str


@dataclass(frozen=True)
class FailureMessage:
    """Split user-facing recovery guidance from raw diagnostics."""

    category: str
    user_message: str
    technical_detail: str
    retryable: bool
    recoverable: bool


class ErrorClassifier:
    """Classify tool/provider errors for retry and handoff decisions."""

    def classify(self, text: str) -> ErrorAssessment:
        lowered = text.lower()
        if "[permission denied]" in lowered or "不可逆" in lowered:
            return ErrorAssessment("permission", retryable=False, recoverable=False, summary=text)
        if any(token in lowered for token in ["timeout", "timed out", "rate limit", "429"]):
            return ErrorAssessment("transient", retryable=True, recoverable=True, summary=text)
        tool_input_tokens = ["old_string not found", "invalid json", "file not found"]
        if any(token in lowered for token in tool_input_tokens):
            return ErrorAssessment("tool_input", retryable=False, recoverable=True, summary=text)
        if any(token in lowered for token in ["model", "provider", "api"]):
            return ErrorAssessment("provider", retryable=True, recoverable=True, summary=text)
        return ErrorAssessment("unknown", retryable=False, recoverable=True, summary=text)


def format_llm_failure(exc: Exception) -> FailureMessage:
    """Create a useful failure message without exposing provider internals by default."""
    technical_detail = f"{type(exc).__name__}: {exc}"
    lowered = technical_detail.lower()
    is_timeout = any(token in lowered for token in ["timeout", "timed out", "connection timed out"])
    seconds = _extract_timeout_seconds(technical_detail)
    if is_timeout:
        wait_text = _format_wait_time(seconds)
        user_message = (
            f"[Ming: 模型服务 {wait_text}没有响应]\n"
            "我已停止本轮执行，并已保留当前进度、trace、checkpoint 和已经写入的文件。\n"
            "可以直接重试；如果连续出现，建议切换模型、缩小任务范围，或从已生成的文件继续检查。"
        )
        return FailureMessage(
            category="timeout",
            user_message=user_message,
            technical_detail=technical_detail,
            retryable=True,
            recoverable=True,
        )

    user_message = (
        "[Ming: 模型调用失败，已停止本轮执行]\n"
        "我已保留当前进度、trace、checkpoint 和已经写入的文件。\n"
        "可以重试本轮任务；如果反复失败，建议切换模型或缩小任务范围。"
    )
    assessment = ErrorClassifier().classify(technical_detail)
    return FailureMessage(
        category=assessment.category,
        user_message=user_message,
        technical_detail=technical_detail,
        retryable=assessment.retryable,
        recoverable=assessment.recoverable,
    )


def format_tool_stall(
    assessment: ProgressAssessment,
    events: list[ToolEvent],
) -> FailureMessage:
    """Explain a no-progress tool stall in product language."""
    recent_events = events[-3:]
    tool_names = _join_unique(event.tool_name for event in recent_events) or "工具"
    if _has_tool_strategy_error(recent_events):
        user_message = (
            "[Ming: 工具调用格式或写入策略失败]\n"
            "这更像是 Ming 内部执行策略问题：刚才的工具参数格式不合法，"
            "或尝试用不稳定的长命令写入内容。\n"
            f"刚才主要尝试了：{tool_names}。\n"
            "我已停止本轮，避免继续用同一种错误方式空转。建议直接重试；"
            "Ming 应改用有效 JSON 的 file_write/file_edit，或把大文件分块写入。"
        )
        return FailureMessage(
            category="tool_strategy_error",
            user_message=user_message,
            technical_detail=_format_tool_stall_detail(assessment, recent_events),
            retryable=True,
            recoverable=True,
        )

    user_message = (
        "[Ming: 我暂停了本轮执行]\n"
        "连续 3 次工具调用没有拿到可用的新信息，所以我先停下来，避免继续空转。\n"
        f"刚才主要尝试了：{tool_names}。\n"
        "建议：换一种工具、缩小目标，或补充文件、链接、运行方式后继续。"
    )
    return FailureMessage(
        category="tool_stall",
        user_message=user_message,
        technical_detail=_format_tool_stall_detail(assessment, recent_events),
        retryable=False,
        recoverable=True,
    )


def _has_tool_strategy_error(events: list[ToolEvent]) -> bool:
    return any(event.progress in {"tool_input_error", "tool_strategy_error"} for event in events)


def _format_tool_stall_detail(
    assessment: ProgressAssessment,
    recent_events: list[ToolEvent],
) -> str:
    return json.dumps(
        {
            "assessment": asdict(assessment),
            "recent_events": [asdict(event) for event in recent_events],
        },
        ensure_ascii=False,
        indent=2,
    )


def _extract_timeout_seconds(text: str) -> float | None:
    marker = "Timeout passed="
    if marker not in text:
        return None
    suffix = text.split(marker, 1)[1]
    number = suffix.split(",", 1)[0].strip()
    try:
        return float(number)
    except ValueError:
        return None


def _format_wait_time(seconds: float | None) -> str:
    if seconds is None:
        return "长时间"
    minutes = round(seconds / 60)
    if minutes >= 1 and abs(seconds - minutes * 60) < 5:
        return f"{minutes} 分钟"
    return f"{seconds:.0f} 秒"


def _join_unique(values) -> str:
    unique: list[str] = []
    for value in values:
        if value and value not in unique:
            unique.append(value)
    return "、".join(unique)


class FileSnapshotStore:
    """Persist pre-change file states so the latest file tool change can roll back."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._stack: list[Path] = []

    def snapshot(self, path: str | Path) -> Path:
        target = Path(path)
        snapshot = FileSnapshot(
            snapshot_id=datetime.now().strftime("%Y%m%d_%H%M%S_%f"),
            path=str(target),
            existed=target.exists(),
            content=target.read_text(encoding="utf-8", errors="replace")
            if target.exists() and target.is_file()
            else "",
        )
        snapshot_path = self.root / f"{snapshot.snapshot_id}.json"
        snapshot_path.write_text(
            json.dumps(asdict(snapshot), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._stack.append(snapshot_path)
        return snapshot_path

    def rollback_latest(self) -> dict[str, int | str]:
        path = self._latest_snapshot_path()
        if path is None:
            return {"rolled_back": 0, "reason": "no snapshot"}

        data = json.loads(path.read_text(encoding="utf-8"))
        target = Path(data["path"])
        if data["existed"]:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(data.get("content", ""), encoding="utf-8")
        elif target.exists() and target.is_file():
            target.unlink()

        path.unlink(missing_ok=True)
        if path in self._stack:
            self._stack.remove(path)
        return {"rolled_back": 1, "path": str(target)}

    def _latest_snapshot_path(self) -> Path | None:
        while self._stack:
            path = self._stack[-1]
            if path.exists():
                return path
            self._stack.pop()

        snapshots = sorted(self.root.glob("*.json"), key=lambda p: p.stat().st_mtime)
        return snapshots[-1] if snapshots else None
