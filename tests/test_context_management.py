import json

import pytest

from ming.config import AgentConfig, LLMConfig, MingConfig
from ming.context.assembler import ContextAssembler, ContextAssemblyInput
from ming.context.manager import ContextManager
from ming.core.agent import Agent
from ming.core.llm import LLMResponse, Message
from ming.core.notepad import NotepadStore
from ming.core.todo import TodoState
from ming.memory.store import MemoryStore


def test_context_assembler_orders_base_session_dialog_and_instant_workbench(tmp_path):
    notes = tmp_path / "notes.md"
    notes.write_text("## Evidence\n- file_read: README says pytest\n", encoding="utf-8")

    messages = ContextAssembler().assemble(
        ContextAssemblyInput(
            base=[Message(role="system", content="base")],
            session=[Message(role="system", content="[memories]\nprefer pytest")],
            dialog=[Message(role="user", content="hello")],
            instant="当前请求：创建测试",
            todo="- [in_progress] 写测试",
            notepad_path=notes,
            tool_names=["file_read", "file_write"],
            pinned_evidence=["README.md: pytest"],
        )
    )

    assert [m.content.splitlines()[0] for m in messages] == [
        "base",
        "[memories]",
        "[instant]",
        "[todo]",
        "[notepad]",
        "[pinned evidence]",
        "[toolset]",
        "hello",
    ]
    assert "file_write" in messages[-2].content


def test_context_manager_has_instant_layer_and_pinned_evidence():
    manager = ContextManager()
    manager.set_base("base")
    manager.add_message(Message(role="user", content="dialog"))
    manager.set_instant_context("当前请求：分析 README")
    manager.pin_evidence("README.md: line 1 important")

    messages = manager.get_messages()

    assert any(m.content.startswith("[instant]") for m in messages)
    assert any("README.md: line 1 important" in m.content for m in messages)

    removed = manager.clear_instant_context()
    assert removed == 1
    assert not any(m.content.startswith("[instant]") for m in manager.get_messages())


def test_todo_state_tracks_steps_and_tool_progress():
    todo = TodoState.from_user_input("创建文件，然后读取确认，并总结结果")

    assert len(todo.items) >= 3
    assert todo.items[0].status == "in_progress"

    todo.mark_step_completed("file_write")

    assert todo.items[0].status == "completed"
    assert any(item.status == "in_progress" for item in todo.items)


def test_notepad_store_records_structured_context(tmp_path):
    store = NotepadStore(tmp_path)
    path = store.create("turn-1", "创建文件")

    store.add_assumption(path, "用户希望保留 scratch 文件")
    store.add_evidence(path, "file_read", "scratch/demo.txt exists")
    store.add_blocker(path, "缺少 API key")

    text = path.read_text(encoding="utf-8")
    summary = store.summary(path)

    assert "## Assumptions" in text
    assert "## Evidence" in text
    assert "## Blockers" in text
    assert "scratch/demo.txt exists" in summary


@pytest.mark.asyncio
async def test_compaction_preserves_pinned_evidence_and_verifies_summary(monkeypatch):
    manager = ContextManager(max_context_tokens=100, compaction_threshold=0.2)
    manager.set_base("base")
    manager.pin_evidence("CRITICAL_EVIDENCE: keep this")
    for i in range(30):
        role = "tool" if i % 3 == 0 else "user"
        manager.add_message(Message(role=role, content=f"message {i} " + "x" * 220))

    async def fake_llm_call(messages, config=None):
        assert "CRITICAL_EVIDENCE" in messages[0].content
        return LLMResponse(
            content="summary with CRITICAL_EVIDENCE: keep this",
            finish_reason="stop",
        )

    await manager.compact(fake_llm_call)

    combined = "\n".join(message.content for message in manager.get_messages())
    assert "CRITICAL_EVIDENCE" in combined
    assert manager.last_compaction_verified is True


def test_memory_store_scope_retrieval_and_project_context(tmp_path):
    store = MemoryStore(tmp_path)
    store.save("user_pref", "pytest", "user", "我喜欢 pytest")
    store.save("project_layout", "core", "project", "src/ming/core 是核心")
    store.save("global_tip", "agents", "global", "先写测试")

    assert "pytest" in store.get_scoped_context(["user"])
    assert "src/ming/core" in store.get_scoped_context(["project"])
    assert "先写测试" not in store.get_scoped_context(["user", "project"])


def test_memory_store_marks_stale_memory_as_review_needed_and_orders_it_last(tmp_path):
    store = MemoryStore(tmp_path)
    stale_path = store.save("old_layout", "old core path", "project", "src/ming/core/gate.py")
    store.save("new_layout", "new router path", "project", "src/ming/core/cognitive_router.py")

    store.mark_stale(stale_path, reason="Gate renamed to CognitiveRouter")

    entries = store.get_by_types(["project"])
    assert entries[-1].name == "old_layout"
    assert entries[-1].stale is True
    assert entries[-1].stale_reason == "Gate renamed to CognitiveRouter"

    context = store.get_scoped_context(["project"])
    assert context.index("new_layout") < context.index("old_layout")
    assert "待复核记忆" in context
    assert "Gate renamed to CognitiveRouter" in context
    assert store.get_stale()[0].name == "old_layout"


def test_agent_switches_active_context_scopes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = MingConfig(llm=LLMConfig(model="test-model", api_key="test"))

    memory = MemoryStore(tmp_path / ".ming" / "memory")
    memory.save("user_pref", "pytest", "user", "我喜欢 pytest")
    memory.save("project_layout", "core", "project", "src/ming/core 是核心")

    agent = Agent(config=config, working_dir=str(tmp_path))
    assert "pytest" in "\n".join(message.content for message in agent.context.session_layer)
    assert "src/ming/core" in "\n".join(message.content for message in agent.context.session_layer)

    result = agent.set_context_scopes(["project"])

    assert result["active_scopes"] == ["project"]
    session_context = "\n".join(message.content for message in agent.context.session_layer)
    assert "pytest" not in session_context
    assert "src/ming/core" in session_context


@pytest.mark.asyncio
async def test_agent_uses_context_workbench_and_can_resume_checkpoint(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = MingConfig(
        llm=LLMConfig(model="test-model", api_key="test"),
        agent=AgentConfig(max_iterations=5),
    )
    seen_messages = []
    calls = 0

    async def fake_llm(messages, config, tools=None):
        nonlocal calls
        calls += 1
        seen_messages.append(messages)
        if calls == 1:
            return LLMResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "file_write",
                            "arguments": json.dumps({"path": "out.txt", "content": "hello"}),
                        },
                    }
                ],
            )
        if calls == 2:
            return LLMResponse(content="FINAL: 已写入 out.txt", finish_reason="stop")
        return LLMResponse(content="PASS: 工具结果支持最终答复", finish_reason="stop")

    monkeypatch.setattr("ming.core.agent.call_llm", fake_llm)

    agent = Agent(config=config, working_dir=str(tmp_path))
    result = await agent.chat("创建 out.txt，然后读取确认")

    assert result == "已写入 out.txt"
    first_call = "\n".join(message.content for message in seen_messages[0])
    assert "[instant]" in first_call
    assert "[todo]" in first_call
    assert "[notepad]" in first_call
    assert "[toolset]" in first_call
    assert "file_write" in first_call

    resumed = Agent(config=config, working_dir=str(tmp_path))
    checkpoint = resumed.resume_latest_checkpoint()

    assert checkpoint is not None
    assert len(resumed.context.dialog_history) > 0
    assert resumed.last_checkpoint_path is not None


@pytest.mark.asyncio
async def test_agent_compaction_circuit_breaker_stops_repeated_failures(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = MingConfig(llm=LLMConfig(model="test-model", api_key="test"))
    agent = Agent(config=config, working_dir=str(tmp_path))
    calls = 0

    async def failing_compact(llm_call):
        nonlocal calls
        calls += 1
        raise RuntimeError("compaction failed")

    monkeypatch.setattr(agent.context, "compact", failing_compact)

    assert await agent._run_compaction(trigger="safety") is False
    assert await agent._run_compaction(trigger="safety") is False
    assert await agent._run_compaction(trigger="safety") is False
    assert await agent._run_compaction(trigger="safety") is False
    assert calls == 3
