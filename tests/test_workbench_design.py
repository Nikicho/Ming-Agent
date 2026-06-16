import json

from ming.core.session_trace import LLMCallMetrics, SessionTrace, ToolCallTrace
from ming.ui.trace_console import TraceConsoleApp, TraceConsoleState


def test_workbench_index_matches_three_zone_interaction_design(tmp_path):
    html = TraceConsoleApp(tmp_path).render_index()

    assert "Ming Agent Workbench" in html
    assert 'id="appShell"' in html
    assert 'id="sessionRail"' in html
    assert 'id="mainWorkspace"' in html
    assert 'id="processRail"' in html
    assert 'id="detailModal"' in html
    assert "做了什么" in html
    assert "异常原因" in html
    assert "SessionTrace" in html
    assert "设置与模型" in html
    assert "模型连接" in html
    assert "LLM API 地址" in html
    assert "模型名称" in html
    assert "API Key" in html
    assert "单次请求超时" in html
    assert "保存到本地设置" in html
    assert "sk-local-demo-key" not in html
    assert '<pre id="settingsPanel">' not in html
    assert 'class="settings-card"' in html
    assert "app-shell sidebar-open" not in html
    assert "app-shell" in html
    assert "panel session-rail" in html
    assert "panel main-workspace" in html
    assert "panel process-rail" in html
    assert "composer-box" in html
    assert "tool-card" in html
    assert "thinking-card" in html
    assert "process-card" in html
    assert "notice" in html
    assert "verdict-card" in html
    assert "modal-backdrop" in html
    assert "<span class=\"chip\">DeepSeek</span>" in html
    assert "&#129504;" in html
    assert "&#128202;" in html
    assert "&#9881;" in html
    assert 'id="toggleProcess" title="切换过程面板 (可锁定)"' in html
    assert 'icon-button active" type="button" id="toggleProcess"' not in html
    assert 'id="runTimeline"' not in html
    assert 'id="liveEvents"' not in html
    assert "SSE 实时事件" not in html
    assert "EventSource" in html
    assert "renderProgressCard" in html
    assert "renderConversationItem" in html
    assert "模型结果" in html
    assert "beginThinking" in html
    assert "appendConversationEvent" in html
    assert "toggleToolCard" in html
    assert "openModal" in html
    assert "setTab" in html
    assert "renderMarkdown" in html
    assert "isMarkdownSeparator" in html
    assert "renderMarkdownTable" in html
    assert "isMarkdownTableStart" in html
    assert "classifyReplyStatus" in html
    assert "计划说明" in html
    assert "暂无产物" in html
    assert "Ming 暂停了" in html
    assert "需要你的判断" in html
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
                "messages": [
                    {"role": "tool", "content": "pre-user noise", "tool_call_id": "old"},
                    {"role": "assistant", "content": "", "tool_calls": []},
                    {
                        "role": "user",
                        "content": "工具调用策略失败，需要换一种执行方式继续，不要把问题交给用户。",
                    },
                    {"role": "assistant", "content": "已创建番茄钟页面"},
                ],
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
    assert "status" not in state["sessions"][0]
    assert state["sessions"][0]["conversation"][0]["role"] == "user"
    assert state["sessions"][0]["conversation"][0]["content"] == "创建番茄钟页面"
    assert "pre-user noise" not in json.dumps(
        state["sessions"][0]["conversation"],
        ensure_ascii=False,
    )
    assert all(
        "工具调用策略失败" not in item.get("content", "")
        for item in state["sessions"][0]["conversation"]
        if isinstance(item.get("content"), str)
    )
    assert state["process_panel"]["todo"]["items"][1]["status"] == "in_progress"
    assert state["process_panel"]["artifacts"]["changed_files"] == ["pomodoro.html"]
    assert state["process_panel"]["context"]["session_trace_path"] == str(trace_path)
    assert "timeline" in state["trace_tabs"]
    assert "exception" in state["trace_tabs"]
    assert "session_trace" in state["trace_tabs"]
    assert state["trace_tabs"]["settings"]["model"]
    assert "api_key" not in state["trace_tabs"]["settings"]
    assert "api_key_configured" in state["trace_tabs"]["settings"]
    assert state["settings"]["model"] == state["trace_tabs"]["settings"]["model"]
    assert any(card["kind"] == "tool" and card["collapsed"] for card in state["timeline"])


def test_history_conversation_keeps_process_out_of_main_chat(tmp_path):
    checkpoint_root = tmp_path / ".ming" / "checkpoints" / "turn-process"
    checkpoint_root.mkdir(parents=True)
    (checkpoint_root / "checkpoint.json").write_text(
        json.dumps(
            {
                "turn_id": "turn-process",
                "name": "build a todo page",
                "created_at": "2026-06-16T23:54:03",
                "messages": [
                    {"role": "user", "content": "build a todo page"},
                    {
                        "role": "assistant",
                        "content": "I will inspect the workspace first.",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {"name": "bash", "arguments": "dir"},
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "call-1",
                        "name": "bash",
                        "content": "Directory listing",
                    },
                    {"role": "assistant", "content": "Done. The todo page is ready."},
                ],
            }
        ),
        encoding="utf-8",
    )

    state = TraceConsoleState(tmp_path).load()
    conversation = state["sessions"][0]["conversation"]
    serialized = json.dumps(conversation, ensure_ascii=False)

    assert conversation == [
        {"role": "user", "content": "build a todo page"},
        {"role": "ming", "content": "Done. The todo page is ready."},
    ]
    assert "tool" not in {item["role"] for item in conversation}
    assert "I will inspect" not in serialized
    assert "Directory listing" not in serialized
