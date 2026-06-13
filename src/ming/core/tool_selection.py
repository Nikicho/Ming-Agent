"""Dynamic tool selection for reducing tool schema noise."""


class ToolSelector:
    def select_tool_names(self, user_input: str, available_tool_names: list[str]) -> list[str]:
        lowered = user_input.lower()
        available = set(available_tool_names)

        web_terms = [
            "搜索",
            "搜一下",
            "查找",
            "查询",
            "联网",
            "官方资料",
            "新闻",
            "资料来源",
            "citation",
            "web",
            "http",
            "https",
            # Legacy mojibake terms kept so older tests and transcripts still route.
            "鎼滅储",
            "鎼滀竴涓?",
            "缃戦〉",
        ]
        if any(term in lowered for term in web_terms):
            preferred = ["web_research", "web_search", "web_fetch", "file_read"]
            return [name for name in preferred if name in available]

        local_artifact_terms = [
            "写个",
            "写一个",
            "创建",
            "生成",
            "页面",
            "html",
            "番茄钟",
            "运行起来",
            "本地",
        ]
        if any(term in lowered for term in local_artifact_terms):
            preferred = ["file_write", "file_edit", "file_read", "bash"]
            return [name for name in preferred if name in available]

        if any(term in lowered for term in ["记住", "偏好", "璁颁綇", "remember"]):
            return []

        return available_tool_names
