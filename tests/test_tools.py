from ming.config import LLMConfig, MingConfig
from ming.core.agent import Agent
from ming.tools.bash import BashTool


def test_shell_tool_description_names_windows_shell_on_windows():
    tool = BashTool()

    assert "Windows" in tool.description


async def test_agent_rolls_back_last_file_write(tmp_path):
    config = MingConfig(llm=LLMConfig(model="test-model", api_key="test"))
    target = tmp_path / "demo.txt"
    target.write_text("old", encoding="utf-8")

    agent = Agent(config=config, working_dir=str(tmp_path))
    result = await agent._execute_permitted_tool(
        "file_write",
        '{"path": "demo.txt", "content": "new"}',
    )

    assert not result.is_error
    assert target.read_text(encoding="utf-8") == "new"

    rollback = agent.rollback_last_change()

    assert rollback["rolled_back"] == 1
    assert target.read_text(encoding="utf-8") == "old"


async def test_agent_rolls_back_new_file_creation(tmp_path):
    config = MingConfig(llm=LLMConfig(model="test-model", api_key="test"))
    target = tmp_path / "created.txt"

    agent = Agent(config=config, working_dir=str(tmp_path))
    result = await agent._execute_permitted_tool(
        "file_write",
        '{"path": "created.txt", "content": "new"}',
    )

    assert not result.is_error
    assert target.exists()

    rollback = agent.rollback_last_change()

    assert rollback["rolled_back"] == 1
    assert not target.exists()
