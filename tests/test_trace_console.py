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

    assert "Ming Trace Console" in html
    assert "/api/state" in html
    assert payload["agent"]["state"] == "idle"
    assert payload["timeline"][0]["kind"] == "empty"
