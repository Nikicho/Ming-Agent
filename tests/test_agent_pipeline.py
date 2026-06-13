import asyncio
import json

import pytest

from ming.config import AgentConfig, ContextConfig, LLMConfig, MingConfig
from ming.core.agent import Agent, AgentProgressEvent
from ming.core.llm import LLMResponse, Message


def test_explicit_remember_saves_memory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = MingConfig(
        llm=LLMConfig(model="test-model", api_key="test"),
        context=ContextConfig(max_context_tokens=10000),
        agent=AgentConfig(max_iterations=5),
    )

    async def fake_llm(**kwargs):
        return LLMResponse(content="我会记住。", finish_reason="stop")

    monkeypatch.setattr("ming.core.agent.call_llm", fake_llm)

    agent = Agent(config=config, working_dir=str(tmp_path))
    result = agent.chat_sync("记住我喜欢用 pytest 而不是 unittest")

    assert "记住" in result
    saved = list((tmp_path / ".ming" / "memory").glob("*.md"))
    assert len(saved) == 1
    assert "pytest" in saved[0].read_text(encoding="utf-8")


def test_agent_forget_scope_separates_dialog_session_and_memory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = MingConfig(llm=LLMConfig(model="test-model", api_key="test"))

    agent = Agent(config=config, working_dir=str(tmp_path))
    agent.context.add_session_context("temporary session note", label="scratch")
    agent.context.add_message(Message(role="user", content="hello"))
    agent.memory.save("user_pref", "pytest", "user", "我喜欢 pytest")
    agent.memory.save("project_note", "module boundary", "project", "src/ming/core")

    cleared = agent.clear_dialog()
    assert cleared == 1
    assert agent.context.dialog_history == []
    assert len(agent.context.session_layer) == 1

    result = agent.forget_scope("session")
    assert result["session_context_removed"] == 1
    assert agent.context.session_layer == []
    assert len(agent.memory.get_all()) == 2

    result = agent.forget_scope("memory")
    assert result["memory_removed"] == 1
    assert [entry.type for entry in agent.memory.get_all()] == ["project"]


@pytest.mark.asyncio
async def test_t1_self_check_runs_before_final_output(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = MingConfig(
        llm=LLMConfig(model="test-model", api_key="test"),
        agent=AgentConfig(max_iterations=5),
    )
    calls = []

    async def fake_llm(messages, config, tools=None):
        calls.append((messages, tools))
        if len(calls) == 1:
            return LLMResponse(content="草稿答案", finish_reason="stop")
        return LLMResponse(content="FINAL: 修正后的答案", finish_reason="stop")

    monkeypatch.setattr("ming.core.agent.call_llm", fake_llm)

    agent = Agent(config=config, working_dir=str(tmp_path))
    result = await agent.chat("解释一下当前项目")

    assert result == "修正后的答案"
    assert len(calls) == 2
    assert "T1" in calls[1][0][-1].content
    assert calls[1][1] is None


@pytest.mark.asyncio
async def test_t3_fact_check_runs_after_tool_backed_turn(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = MingConfig(
        llm=LLMConfig(model="test-model", api_key="test"),
        agent=AgentConfig(max_iterations=5),
    )
    calls = []

    async def fake_llm(messages, config, tools=None):
        calls.append((messages, tools))
        if len(calls) == 1:
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
        if len(calls) == 2:
            return LLMResponse(content="FINAL: 已写入 out.txt", finish_reason="stop")
        return LLMResponse(content="PASS: 工件和结论一致", finish_reason="stop")

    monkeypatch.setattr("ming.core.agent.call_llm", fake_llm)

    agent = Agent(config=config, working_dir=str(tmp_path))
    result = await agent.chat("创建 out.txt")

    assert result == "已写入 out.txt"
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "hello"
    assert len(calls) == 3
    assert "T3" in calls[2][0][0].content
    assert calls[2][1] is None


@pytest.mark.asyncio
async def test_agent_stops_after_repeated_no_signal_tool_calls(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = MingConfig(
        llm=LLMConfig(model="test-model", api_key="test"),
        agent=AgentConfig(max_iterations=10),
    )
    calls = 0

    async def fake_llm(messages, config, tools=None):
        nonlocal calls
        calls += 1
        return LLMResponse(
            content="",
            finish_reason="tool_calls",
            tool_calls=[
                {
                    "id": f"call-{calls}",
                    "type": "function",
                    "function": {
                        "name": "bash",
                        "arguments": json.dumps({"command": f"echo {calls}"}),
                    },
                }
            ],
        )

    monkeypatch.setattr("ming.core.agent.call_llm", fake_llm)

    agent = Agent(config=config, working_dir=str(tmp_path))
    result = await agent.chat("搜一下不存在的资料")

    assert "工具循环已停止" in result
    assert calls == 3


@pytest.mark.asyncio
async def test_agent_emits_summary_progress_events(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = MingConfig(
        llm=LLMConfig(model="test-model", api_key="test"),
        agent=AgentConfig(max_iterations=5),
    )
    calls = 0
    events: list[AgentProgressEvent] = []

    async def fake_llm(messages, config, tools=None):
        nonlocal calls
        calls += 1
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

    agent = Agent(config=config, working_dir=str(tmp_path), progress_callback=events.append)
    result = await agent.chat("创建 out.txt")

    assert result == "已写入 out.txt"
    assert [event.stage for event in events] == [
        "context",
        "route",
        "llm",
        "tool",
        "llm",
        "verify",
        "done",
    ]
    assert any(event.message == "执行工具 file_write" for event in events)
    assert all(event.turn_id for event in events)
    assert len({event.turn_id for event in events}) == 1


@pytest.mark.asyncio
async def test_agent_returns_graceful_message_and_trace_on_llm_failure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = MingConfig(
        llm=LLMConfig(model="test-model", api_key="test"),
        agent=AgentConfig(max_iterations=5),
    )

    async def failing_llm(messages, config, tools=None):
        raise RuntimeError("provider disconnected")

    monkeypatch.setattr("ming.core.agent.call_llm", failing_llm)

    agent = Agent(config=config, working_dir=str(tmp_path))
    result = await agent.chat("帮我写一个番茄钟页面")

    assert "模型调用失败" in result
    assert "provider disconnected" in result
    trace_files = list((tmp_path / ".ming" / "traces").glob("*.json"))
    checkpoint_files = list((tmp_path / ".ming" / "checkpoints").glob("*/checkpoint.json"))
    assert trace_files
    assert checkpoint_files
    assert "llm_error" in trace_files[0].read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_agent_saves_cancelled_turn_when_user_stops_thinking(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = MingConfig(
        llm=LLMConfig(model="test-model", api_key="test"),
        agent=AgentConfig(max_iterations=5),
    )

    async def cancelled_llm(messages, config, tools=None):
        raise asyncio.CancelledError()

    monkeypatch.setattr("ming.core.agent.call_llm", cancelled_llm)

    agent = Agent(config=config, working_dir=str(tmp_path))
    result = await agent.chat("帮我写一个番茄钟页面")

    assert "已停止本轮思考" in result
    trace_files = list((tmp_path / ".ming" / "traces").glob("*.json"))
    assert trace_files
    trace_text = trace_files[0].read_text(encoding="utf-8")
    assert "cancelled" in trace_text
    assert "completed" not in trace_text
