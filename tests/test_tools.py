from ming.tools.bash import BashTool


def test_shell_tool_description_names_windows_shell_on_windows():
    tool = BashTool()

    assert "Windows" in tool.description
