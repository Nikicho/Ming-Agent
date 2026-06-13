import json

from ming.core.llm import Message
from ming.core.trace import CheckpointStore, RunTrace
from ming.ui.trace_console import TraceConsoleApp, TraceConsoleState


def test_trace_console_builds_current_agent_state(tmp_path):
    trace = RunTrace("turn-1", "创建 scratch/trace_demo.txt，然后读取确认")
    trace.tool_events.append({
        "event_id": "evt-1",
        "tool_name": "file_write",
        "action": "file_write",
        "status": "ok",
        "output_chars": 42,
        "evidence_count": 0,
        "progress": "unknown",
    })
    trace.add_observation("evidence", "file_read confirmed trace_demo.txt")
    trace.add_assessment("continue", "工具写入有输出，继续核验")
    trace.final_output = "已创建并读取确认。"
    trace_path = trace.save(tmp_path / ".ming" / "traces")

    notepad_path = tmp_path / ".ming" / "scratch" / "turn-1" / "notes.md"
    notepad_path.parent.mkdir(parents=True)
    notepad_path.write_text("# Notes\n", encoding="utf-8")
    CheckpointStore(tmp_path / ".ming" / "checkpoints").save(
        "turn-1",
        [Message(role="user", content=trace.user_input)],
        trace_path,
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
    assert any(card["kind"] == "tool" and card["id"] == "evt-1" for card in state["timeline"])
    assert any(agent["name"] == "Alpha" for agent in state["subagents"])
    assert state["artifacts"]["trace_path"].endswith("turn-1.json")
    assert state["artifacts"]["changed_files"] == ["scratch/trace_demo.txt"]


def test_trace_console_app_renders_index_and_json(tmp_path):
    app = TraceConsoleApp(tmp_path)

    html = app.render_index()
    payload = json.loads(app.state_json())

    assert "Ming 任务工作台" in html
    assert "/api/state" in html
    assert "EventSource" in html
    assert "/api/events" in html
    assert "liveEvents" in html
    assert "chatForm" in html
    assert "messageInput" in html
    assert "stopTurnBtn" in html
    assert "conversation" in html
    assert "任务工作台" in html
    assert "执行过程" in html
    assert "诊断详情" in html
    assert "runTimeline" in html
    assert "formatRunEvent" in html
    assert "renderRunTimeline" in html
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
