"""Permission gate for dangerous operations."""

import json
import re
from dataclasses import dataclass

IRREVERSIBLE_COMMANDS = [
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+push\s+--force\b",
    r"\brm\s+-rf\b",
    r"\bdel\s+/[fsq]\b",
    r"\brmdir\s+/s\b",
    r"\bformat\b",
    r"\bdrop\s+database\b",
    r"\btruncate\b",
]


@dataclass
class PermissionDecision:
    allowed: bool
    reason: str = ""


class PermissionGate:
    """Hard gate for operations that should require explicit approval."""

    def evaluate(self, tool_name: str, tool_args: str) -> PermissionDecision:
        if tool_name != "bash":
            return PermissionDecision(True)

        try:
            data = json.loads(tool_args) if tool_args else {}
        except json.JSONDecodeError:
            return PermissionDecision(True)

        command = str(data.get("command", "")).lower()
        for pattern in IRREVERSIBLE_COMMANDS:
            if re.search(pattern, command):
                return PermissionDecision(False, "检测到不可逆或高风险命令，需要人类明确批准。")

        return PermissionDecision(True)
