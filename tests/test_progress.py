from ming.core.progress import ProgressTracker, ToolEvent


def test_progress_tracker_stops_after_three_no_signal_events_for_same_goal():
    tracker = ProgressTracker(max_no_signal_streak=3)

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
        for _ in range(3)
    ]

    assert decisions[0].decision == "continue"
    assert decisions[1].decision == "continue"
    assert decisions[2].decision == "stop"
    assert "连续" in decisions[2].reason


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
