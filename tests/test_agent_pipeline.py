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


def test_agent_instant_context_declares_local_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = MingConfig(llm=LLMConfig(model="test-model", api_key="test"))
    agent = Agent(config=config, working_dir=str(tmp_path))

    context = agent._build_instant_context("读取当前 HTML 文件")

    assert str(tmp_path) in context
    assert "当前工作文件夹" in context
    assert "不要把本机工作台描述为沙盒" in context


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
async def test_agent_rejects_plan_only_answer_for_execution_task(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = MingConfig(
        llm=LLMConfig(model="test-model", api_key="test"),
        agent=AgentConfig(max_iterations=6),
    )
    calls = []

    async def fake_llm(messages, config, tools=None):
        calls.append((messages, tools))
        if len(calls) == 1:
            return LLMResponse(
                content="方案：创建 `pomodoro.html`，然后运行本地服务器。",
                finish_reason="stop",
            )
        if len(calls) == 2:
            assert "只给了计划" in messages[-1].content
            return LLMResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "file_write",
                            "arguments": json.dumps(
                                {"path": "pomodoro.html", "content": "<h1>Pomodoro</h1>"}
                            ),
                        },
                    }
                ],
            )
        if len(calls) == 3:
            return LLMResponse(
                content="已完成：已创建 `pomodoro.html`，可以打开验证。",
                finish_reason="stop",
            )
        return LLMResponse(content="PASS: 工具结果支持最终答复。", finish_reason="stop")

    monkeypatch.setattr("ming.core.agent.call_llm", fake_llm)

    agent = Agent(config=config, working_dir=str(tmp_path))
    result = await agent.chat("帮我写一个简单番茄钟页面，并运行起来")

    assert result.startswith("已完成")
    assert (tmp_path / "pomodoro.html").exists()
    checkpoint = agent.checkpoints.load(agent.last_checkpoint_path)
    assert checkpoint["changed_files"] == ["pomodoro.html"]
    assert checkpoint["todo"]["items"][0]["status"] == "completed"
    assert list((tmp_path / ".ming" / "session_traces").glob("*.json"))


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

    assert "我暂停了本轮执行" in result
    assert "连续 3 次工具调用没有拿到可用的新信息" in result
    assert "no_signal" not in result
    assert calls == 3
    checkpoint = agent.checkpoints.load(agent.last_checkpoint_path)
    assert checkpoint["todo"]["items"][0]["status"] != "completed"


@pytest.mark.asyncio
async def test_agent_replans_after_tool_argument_failures_before_stopping(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = MingConfig(
        llm=LLMConfig(model="test-model", api_key="test"),
        agent=AgentConfig(max_iterations=8),
    )
    calls = 0

    async def fake_llm(messages, config, tools=None):
        nonlocal calls
        calls += 1
        if calls <= 2:
            return LLMResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    {
                        "id": f"bad-{calls}",
                        "type": "function",
                        "function": {
                            "name": "file_write",
                            "arguments": '{"path": "out.html", "content": "unterminated',
                        },
                    }
                ],
            )
        if calls == 3:
            assert "工具调用策略失败" in messages[-1].content
            assert "不要继续用损坏的 JSON" in messages[-1].content
            return LLMResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    {
                        "id": "good-1",
                        "type": "function",
                        "function": {
                            "name": "file_write",
                            "arguments": json.dumps({"path": "out.html", "content": "ok"}),
                        },
                    }
                ],
            )
        if calls == 4:
            return LLMResponse(content="FINAL: 已写入 out.html", finish_reason="stop")
        return LLMResponse(content="PASS: 文件写入结果支持最终回复", finish_reason="stop")

    monkeypatch.setattr("ming.core.agent.call_llm", fake_llm)

    agent = Agent(config=config, working_dir=str(tmp_path))
    result = await agent.chat("创建 out.html")

    assert result == "已写入 out.html"
    assert (tmp_path / "out.html").read_text(encoding="utf-8") == "ok"
    assert calls == 5


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
        "thought",
        "tool",
        "llm",
        "thought",
        "verify",
        "done",
    ]
    assert any(event.message == "执行工具 file_write" for event in events)
    thought_events = [event for event in events if event.stage == "thought"]
    assert "准备调用工具：file_write" in thought_events[0].detail
    assert "已写入 out.txt" in thought_events[1].detail
    assert all(event.turn_id for event in events)
    assert len({event.turn_id for event in events}) == 1


@pytest.mark.asyncio
async def test_agent_accepts_external_turn_id_for_web_runtime(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = MingConfig(
        llm=LLMConfig(model="test-model", api_key="test"),
        agent=AgentConfig(max_iterations=5),
    )
    events: list[AgentProgressEvent] = []

    async def fake_llm(messages, config, tools=None):
        return LLMResponse(content="FINAL: ok", finish_reason="stop")

    monkeypatch.setattr("ming.core.agent.call_llm", fake_llm)

    agent = Agent(config=config, working_dir=str(tmp_path), progress_callback=events.append)
    result = await agent.chat("hello", turn_id="turn-web-1")

    assert result == "ok"
    assert {event.turn_id for event in events} == {"turn-web-1"}
    checkpoint_files = list((tmp_path / ".ming" / "checkpoints").glob("*/checkpoint.json"))
    assert checkpoint_files


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
    assert "provider disconnected" not in result
    checkpoint_files = list((tmp_path / ".ming" / "checkpoints").glob("*/checkpoint.json"))
    assert checkpoint_files
    session = agent.session_trace.to_dict()
    assert "模型调用失败" in session["turns"][0]["final_output"]


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
    checkpoint_files = list((tmp_path / ".ming" / "checkpoints").glob("*/checkpoint.json"))
    assert checkpoint_files
