from ming.core.progress import ProgressTracker, ToolEvent


def test_progress_tracker_nudges_after_repeated_no_signal_events():
    tracker = ProgressTracker(max_no_signal_streak=5, strong_nudge_threshold=8)

    decisions = [
        tracker.record(
            ToolEvent(
                tool_name="web_search",
                action="search",
                status="ok",
                output_chars=12,
                evidence_count=0,
                progress="no_signal",
            )
        )
        for _ in range(8)
    ]

    assert [decision.decision for decision in decisions[:4]] == ["continue"] * 4
    assert decisions[4].decision == "nudge"
    assert decisions[5].decision == "continue"
    assert decisions[6].decision == "continue"
    assert decisions[7].decision == "nudge_strong"
    assert "连续" in decisions[4].reason


def test_tool_event_treats_successful_write_and_shell_as_progress():
    write_event = ToolEvent.from_tool_result(
        tool_name="file_write",
        tool_args='{"path": "out.txt", "content": "ok"}',
        output="ok",
        is_error=False,
    )
    shell_event = ToolEvent.from_tool_result(
        tool_name="bash",
        tool_args='{"command": "python -m pytest"}',
        output="OK",
        is_error=False,
    )

    assert write_event.progress == "new_evidence"
    assert shell_event.progress == "new_evidence"


def test_tool_event_classifies_web_search_evidence_from_json_output():
    event = ToolEvent.from_tool_result(
        tool_name="web_search",
        tool_args='{"query": "agent"}',
        output='{"results": [{"title": "A", "url": "https://example.com"}]}',
        is_error=False,
    )

    assert event.status == "ok"
    assert event.evidence_count == 1
    assert event.progress == "new_evidence"


def test_tool_event_classifies_invalid_json_as_tool_input_error():
    event = ToolEvent.from_tool_result(
        tool_name="file_write",
        tool_args='{"path": "out.html", "content": "unterminated',
        output="Invalid JSON arguments: Unterminated string starting at: line 1 column 35",
        is_error=True,
    )

    assert event.status == "error"
    assert event.progress == "tool_input_error"
    assert "Invalid JSON arguments" in event.diagnostic


def test_progress_tracker_replans_before_stopping_tool_strategy_failures():
    tracker = ProgressTracker(max_no_signal_streak=3)

    first = tracker.record(
        ToolEvent(
            tool_name="file_write",
            action="file_write",
            status="error",
            output_chars=83,
            evidence_count=0,
            progress="tool_input_error",
            diagnostic="Invalid JSON arguments: Unterminated string",
        )
    )
    second = tracker.record(
        ToolEvent(
            tool_name="bash",
            action="bash",
            status="error",
            output_chars=34,
            evidence_count=0,
            progress="tool_strategy_error",
            diagnostic="Command line is too long",
        )
    )

    assert first.decision == "continue"
    assert second.decision == "replan"
    assert "工具调用策略失败" in second.reason
