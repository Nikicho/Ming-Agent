import json

import pytest

from ming.config import AgentConfig, LLMConfig, MingConfig
from ming.core.agent import Agent
from ming.core.llm import LLMResponse
from ming.core.permission import PermissionGate
from ming.core.tool_selection import ToolSelector


def test_permission_gate_blocks_irreversible_shell_commands():
    decision = PermissionGate().evaluate("bash", '{"command": "git reset --hard HEAD~1"}')

    assert decision.allowed is False
    assert "不可逆" in decision.reason


def test_tool_selector_prefers_web_tools_for_search_requests():
    selected = ToolSelector().select_tool_names(
        "请搜索 MCP tools 官方文档",
        available_tool_names=["bash", "file_read", "web_search", "web_fetch"],
    )

    assert "web_search" in selected
    assert "web_fetch" in selected
    assert "bash" not in selected


def test_tool_selector_keeps_local_page_tasks_on_file_tools():
    selected = ToolSelector().select_tool_names(
        "帮我写个简单页面，并且运行起来，页面是个番茄钟",
        available_tool_names=[
            "bash",
            "file_read",
            "file_write",
            "file_edit",
            "web_search",
            "web_fetch",
            "web_research",
        ],
    )

    assert "file_write" in selected
    assert "bash" in selected
    assert "web_research" not in selected
    assert "web_search" not in selected


@pytest.mark.asyncio
async def test_agent_persists_trace_checkpoint_todo_and_notepad(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = MingConfig(
        llm=LLMConfig(model="test-model", api_key="test"),
        agent=AgentConfig(max_iterations=5),
    )
    calls = 0

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

    agent = Agent(config=config, working_dir=str(tmp_path))
    result = await agent.chat("创建 out.txt")

    assert result == "已写入 out.txt"
    trace_files = list((tmp_path / ".ming" / "traces").glob("*.json"))
    checkpoint_files = list((tmp_path / ".ming" / "checkpoints").glob("*/checkpoint.json"))
    note_files = list((tmp_path / ".ming" / "scratch").glob("*/notes.md"))

    assert len(trace_files) == 1
    assert len(checkpoint_files) == 1
    assert len(note_files) == 1
    assert "创建 out.txt" in note_files[0].read_text(encoding="utf-8")

    trace = json.loads(trace_files[0].read_text(encoding="utf-8"))
    assert trace["tool_events"][0]["tool_name"] == "file_write"
    assert trace["final_output"] == "已写入 out.txt"

    checkpoint = json.loads(checkpoint_files[0].read_text(encoding="utf-8"))
    assert checkpoint["todo"]["items"][0]["status"] == "completed"
    assert checkpoint["trace_path"].endswith(".json")
