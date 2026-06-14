import json

from ming.core.session_trace import LLMCallMetrics, SessionTrace, ToolCallTrace
from ming.ui.trace_console import TraceConsoleApp, TraceConsoleState


def test_workbench_index_matches_three_zone_interaction_design(tmp_path):
    html = TraceConsoleApp(tmp_path).render_index()

    assert "Ming Agent Workbench" in html
    assert 'id="sessionRail"' in html
    assert 'id="mainWorkspace"' in html
    assert 'id="processRail"' in html
    assert 'id="brainModal"' in html
    assert "做了什么" in html
    assert "异常原因" in html
    assert "SessionTrace" in html
    assert "设置与模型" in html
    assert "EventSource" in html
    assert "renderProgressCard" in html
    assert "toggleToolCard" in html
    assert "Ming 的判断分歧" in html
    assert "停止思考" in html


def test_workbench_state_exposes_sessions_process_panel_and_trace_tabs(tmp_path):
    trace = SessionTrace(model="test-model", agent_version="test")
    trace.begin_turn("turn-1", "创建番茄钟页面")
    trace.init_single_path()
    trace.finish_step(
        iteration=1,
        response_content_length=12,
        tool_calls=[
            ToolCallTrace(
                id="call-1",
                name="file_write",
                arguments='{"path":"pomodoro.html"}',
                loop_status="ok",
                consecutive_identical=1,
                result_output_length=80,
            )
        ],
        is_final=False,
        metrics=LLMCallMetrics(prompt_tokens=10, completion_tokens=5, latency_ms=30),
    )
    trace.finish_turn("已创建番茄钟页面")
    trace_path = trace.save(tmp_path / ".ming" / "session_traces")

    checkpoint_root = tmp_path / ".ming" / "checkpoints" / "turn-1"
    checkpoint_root.mkdir(parents=True)
    (checkpoint_root / "checkpoint.json").write_text(
        json.dumps(
            {
                "turn_id": "turn-1",
                "name": "创建番茄钟页面",
                "created_at": "2026-06-14T10:00:00",
                "todo": {
                    "items": [
                        {"text": "创建页面文件", "status": "completed"},
                        {"text": "运行并验证", "status": "in_progress"},
                    ]
                },
                "notepad_path": str(tmp_path / ".ming" / "scratch" / "turn-1" / "notes.md"),
                "changed_files": ["pomodoro.html"],
                "messages_summary": "user: 创建番茄钟页面",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    state = TraceConsoleState(tmp_path).load()

    assert state["schema_version"] == "ming-workbench-v1"
    assert state["sessions"][0]["turn_id"] == "turn-1"
    assert state["sessions"][0]["title"] == "创建番茄钟页面"
    assert state["process_panel"]["todo"]["items"][1]["status"] == "in_progress"
    assert state["process_panel"]["artifacts"]["changed_files"] == ["pomodoro.html"]
    assert state["process_panel"]["context"]["session_trace_path"] == str(trace_path)
    assert "timeline" in state["trace_tabs"]
    assert "exception" in state["trace_tabs"]
    assert "session_trace" in state["trace_tabs"]
    assert any(card["kind"] == "tool" and card["collapsed"] for card in state["timeline"])
