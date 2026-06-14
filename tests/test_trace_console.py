import json

import yaml

from ming.core.llm import Message
from ming.core.session_trace import LLMCallMetrics, SessionTrace, ToolCallTrace
from ming.core.trace import CheckpointStore
from ming.ui.trace_console import TraceConsoleApp, TraceConsoleState, _is_client_disconnect


def test_trace_console_builds_current_agent_state(tmp_path):
    st = SessionTrace(model="test-model", agent_version="0.1.0")
    st.begin_turn("turn-1", "创建 scratch/trace_demo.txt，然后读取确认")
    st.init_single_path()
    st.finish_step(
        iteration=1,
        response_content_length=0,
        tool_calls=[ToolCallTrace(
            id="call-1",
            name="file_write",
            arguments='{"path":"scratch/trace_demo.txt","content":"hello"}',
            loop_status="ok",
            consecutive_identical=1,
            result_output_length=42,
            result_is_error=False,
            latency_ms=100,
        )],
        is_final=False,
        metrics=LLMCallMetrics(prompt_tokens=100, completion_tokens=50, latency_ms=200),
    )
    st.finish_turn("已创建并读取确认。")
    st.save(tmp_path / ".ming" / "session_traces")

    notepad_path = tmp_path / ".ming" / "scratch" / "turn-1" / "notes.md"
    notepad_path.parent.mkdir(parents=True)
    notepad_path.write_text("# Notes\n", encoding="utf-8")
    CheckpointStore(tmp_path / ".ming" / "checkpoints").save(
        "turn-1",
        [Message(role="user", content="创建 scratch/trace_demo.txt，然后读取确认")],
        notepad_path,
        todo={
            "items": [
                {"text": "创建 scratch/trace_demo.txt", "status": "completed"},
                {"text": "读取确认", "status": "completed"},
            ]
        },
        changed_files=["scratch/trace_demo.txt"],
    )

    state = TraceConsoleState(tmp_path).load()

    assert state["current_task"]["turn_id"] == "turn-1"
    assert state["current_task"]["text"] == "创建 scratch/trace_demo.txt，然后读取确认"
    assert state["agent"]["state"] == "completed"
    assert state["todo"]["items"][0]["status"] == "completed"
    assert any(
        card["kind"] == "tool" and "file_write" in card["title"]
        for card in state["timeline"]
    )
    assert any(agent["name"] == "Alpha" for agent in state["subagents"])
    assert state["artifacts"]["changed_files"] == ["scratch/trace_demo.txt"]


def test_trace_console_app_renders_index_and_json(tmp_path):
    app = TraceConsoleApp(tmp_path)

    html = app.render_index()
    payload = json.loads(app.state_json())

    assert "Ming 任务工作台" in html
    assert "/api/state" in html
    assert "EventSource" in html
    assert "/api/events" in html
    assert "chatForm" in html
    assert "messageInput" in html
    assert "stopTurnBtn" in html
    assert "conversation" in html
    assert "任务工作台" in html
    assert "诊断详情" in html
    assert "<span class=\"chip\">DeepSeek</span>" in html
    assert "&#129504;" in html
    assert "&#128202;" in html
    assert "&#9881;" in html
    assert "模型连接" in html
    assert "LLM API 地址" in html
    assert "保存到本地设置" in html
    assert "sk-local-demo-key" not in html
    assert "SSE 实时事件" not in html
    assert 'id="runTimeline"' not in html
    assert 'id="liveEvents"' not in html
    assert "formatRunEvent" in html
    assert "renderRunTimeline" in html
    assert "renderMarkdown" in html
    assert "isMarkdownSeparator" in html
    assert "classifyReplyStatus" in html
    assert "计划说明" in html
    assert "暂无产物" in html
    assert "模型思考" in html
    assert "工具执行" in html
    assert payload["agent"]["state"] == "idle"
    assert payload["timeline"][0]["kind"] == "empty"


def test_trace_console_formats_sse_event(tmp_path):
    app = TraceConsoleApp(tmp_path)
    event = {
        "seq": 1,
        "stage": "llm",
        "message": "调用模型，第 1 轮",
        "turn_id": "turn-1",
    }

    payload = app.format_sse(event)

    assert payload.startswith("id: 1\n")
    assert "event: llm\n" in payload
    assert "data: " in payload
    assert payload.endswith("\n\n")


def test_trace_console_treats_browser_sse_disconnect_as_normal():
    assert _is_client_disconnect(ConnectionAbortedError(10053, "aborted"))
    assert _is_client_disconnect(ConnectionResetError(10054, "reset"))
    assert _is_client_disconnect(BrokenPipeError(32, "broken pipe"))
    assert not _is_client_disconnect(OSError(5, "unrelated"))


def test_trace_console_loads_legacy_traces_directory(tmp_path):
    st = SessionTrace(model="test-model", agent_version="0.1.0")
    st.begin_turn("turn-legacy", "创建页面")
    st.init_single_path()
    st.finish_turn("已完成：创建页面")
    trace_path = st.save(tmp_path / ".ming" / "traces")

    state = TraceConsoleState(tmp_path).load()

    assert state["process_panel"]["context"]["session_trace_path"] == str(trace_path)
    assert state["trace_tabs"]["session_trace"]["schema_version"] == "ming-trace-v1"


def test_trace_console_does_not_attach_unrelated_trace_to_checkpoint(tmp_path):
    st = SessionTrace(model="test-model", agent_version="0.1.0")
    st.begin_turn("older-turn", "旧任务")
    st.init_single_path()
    st.finish_turn("旧结果")
    st.save(tmp_path / ".ming" / "traces")

    CheckpointStore(tmp_path / ".ming" / "checkpoints").save(
        "current-turn",
        [Message(role="user", content="当前任务")],
        tmp_path / ".ming" / "scratch" / "current-turn" / "notes.md",
        todo={"items": [{"text": "当前任务", "status": "in_progress"}]},
    )

    state = TraceConsoleState(tmp_path).load()

    context = state["process_panel"]["context"]
    assert context["session_trace_path"] == ""
    assert context["total_prompt_tokens"] > 0


def test_trace_console_event_stream_reads_live_events(tmp_path):
    app = TraceConsoleApp(tmp_path)
    app.live_events.append(stage="tool", message="执行工具 file_write", turn_id="turn-1")

    chunk = next(app.event_stream(last_seq=0, poll_seconds=0, heartbeat_seconds=999))

    assert "event: tool\n" in chunk
    assert "执行工具 file_write" in chunk


def test_trace_console_event_stream_resumes_after_last_seq(tmp_path):
    app = TraceConsoleApp(tmp_path)
    first = app.live_events.append(stage="context", message="prepare", turn_id="turn-1")
    second = app.live_events.append(stage="tool", message="run", turn_id="turn-1")

    chunk = next(app.event_stream(last_seq=first["seq"], poll_seconds=0, heartbeat_seconds=999))

    assert f"id: {second['seq']}\n" in chunk
    assert "event: tool\n" in chunk
    assert "prepare" not in chunk


def test_trace_console_default_stream_start_skips_existing_history(tmp_path):
    app = TraceConsoleApp(tmp_path)
    app.live_events.append(stage="submitted", message="old", turn_id="turn-1")
    latest = app.live_events.append(stage="done", message="old done", turn_id="turn-1")

    assert app.default_event_start_seq() == latest["seq"]


def test_trace_console_submit_chat_validates_message(tmp_path):
    app = TraceConsoleApp(tmp_path)

    status, payload = app.submit_chat({"message": "  "})

    assert status == 400
    assert payload["status"] == "invalid"


def test_trace_console_submit_chat_returns_accepted_turn(tmp_path):
    class FakeRuntime:
        def submit(self, message):
            self.message = message
            return {"status": "running", "turn_id": "turn-1"}

    runtime = FakeRuntime()
    app = TraceConsoleApp(tmp_path, chat_runtime=runtime)

    status, payload = app.submit_chat({"message": "hello"})

    assert status == 202
    assert payload == {"status": "running", "turn_id": "turn-1"}
    assert runtime.message == "hello"


def test_trace_console_stop_current_turn_maps_runtime_status(tmp_path):
    class FakeRuntime:
        def stop(self):
            return {"status": "cancelled", "turn_id": "turn-1"}

    app = TraceConsoleApp(tmp_path, chat_runtime=FakeRuntime())

    status, payload = app.stop_current_turn()

    assert status == 200
    assert payload == {"status": "cancelled", "turn_id": "turn-1"}


def test_trace_console_save_settings_writes_local_config_without_clearing_api_key(tmp_path):
    local_path = tmp_path / "config" / "local.yaml"
    local_path.parent.mkdir()
    local_path.write_text(
        yaml.safe_dump({"llm": {"api_key": "existing-key"}}, sort_keys=False),
        encoding="utf-8",
    )
    app = TraceConsoleApp(tmp_path)

    status, payload = app.save_settings(
        {
            "model": "deepseek/deepseek-chat",
            "api_base": "https://api.deepseek.com/v1",
            "api_key": "",
            "request_timeout_seconds": "45 秒",
        }
    )

    saved = yaml.safe_load(local_path.read_text(encoding="utf-8"))
    assert status == 200
    assert payload["status"] == "settings_saved"
    assert saved["llm"]["model"] == "deepseek/deepseek-chat"
    assert saved["llm"]["api_base"] == "https://api.deepseek.com/v1"
    assert saved["llm"]["api_key"] == "existing-key"
    assert saved["llm"]["request_timeout_seconds"] == 45


def test_trace_console_save_settings_requires_model(tmp_path):
    app = TraceConsoleApp(tmp_path)

    status, payload = app.save_settings({"model": ""})

    assert status == 400
    assert payload["status"] == "invalid"
