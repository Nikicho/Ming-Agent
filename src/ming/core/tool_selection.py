"""Dynamic tool selection for reducing tool schema noise."""


class ToolSelector:
    def select_tool_names(self, user_input: str, available_tool_names: list[str]) -> list[str]:
        lowered = user_input.lower()
        available = set(available_tool_names)

        if any(term in lowered for term in ["搜索", "搜一下", "网页", "web", "http", "https"]):
            preferred = ["web_search", "web_fetch", "file_read"]
            return [name for name in preferred if name in available]

        if any(term in lowered for term in ["记住", "remember"]):
            return []

        return available_tool_names
