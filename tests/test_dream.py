import json

from ming.core.dream import DreamEngine
from ming.core.llm import Message
from ming.core.trace import CheckpointStore, RunTrace
from ming.memory.store import MemoryStore


def test_dream_engine_writes_review_report_without_mutating_memory(tmp_path):
    memory = MemoryStore(tmp_path / ".ming" / "memory")
    stale_path = memory.save(
        "old_gate_name",
        "old router naming",
        "project",
        "src/ming/core/gate.py still owns cognitive routing",
    )
    memory.mark_stale(stale_path, reason="renamed to CognitiveRouter")
    fresh_path = memory.save(
        "new_router_name",
        "new router naming",
        "project",
        "src/ming/core/cognitive_router.py owns cognitive routing",
    )

    trace = RunTrace("turn-1", "重命名 Gate")
    trace.tool_events.append({
        "event_id": "evt-1",
        "tool_name": "file_edit",
        "status": "ok",
        "progress": "unknown",
    })
    trace.add_assessment("continue", "renaming progressed")
    trace.final_output = "Gate 已改名为 CognitiveRouter"
    trace_path = trace.save(tmp_path / ".ming" / "traces")
    CheckpointStore(tmp_path / ".ming" / "checkpoints").save(
        "turn-1",
        [Message(role="user", content="重命名 Gate")],
        trace_path,
        tmp_path / ".ming" / "scratch" / "turn-1" / "notes.md",
        todo={"items": [{"text": "重命名 Gate", "status": "completed"}]},
        changed_files=["src/ming/core/cognitive_router.py"],
    )

    report_path = DreamEngine(tmp_path).run()
    payload = json.loads(report_path.read_text(encoding="utf-8"))

    assert payload["mode"] == "light"
    assert payload["summary"]["trace_count"] == 1
    assert payload["summary"]["memory_count"] == 2
    assert payload["stale_memory_candidates"][0]["name"] == "old_gate_name"
    assert any("src/ming/core/cognitive_router.py" in item for item in payload["project_lessons"])
    assert "stale: true" in stale_path.read_text(encoding="utf-8")
    assert "stale: true" not in fresh_path.read_text(encoding="utf-8")
