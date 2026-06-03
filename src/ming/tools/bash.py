"""Bash/shell execution tool."""

import asyncio
import os
import subprocess
from typing import Any

from ming.tools.base import Tool, ToolResult

MAX_OUTPUT_CHARS = 50000
DEFAULT_TIMEOUT = 60


class BashTool(Tool):
    @property
    def name(self) -> str:
        return "bash"

    @property
    def description(self) -> str:
        return (
            "Execute a shell command and return its output. "
            "Use for running programs, scripts, git commands, etc. "
            "Commands run in the project working directory. "
            "On Windows this uses the default shell (usually cmd.exe), so prefer "
            "Windows-compatible commands such as dir, type, cd /d, and python."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 60).",
                },
            },
            "required": ["command"],
        }

    def __init__(self, working_dir: str | None = None):
        self.working_dir = working_dir or os.getcwd()

    async def execute(self, command: str, timeout: int = DEFAULT_TIMEOUT, **_: Any) -> ToolResult:
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.working_dir,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)

            output_parts = []
            if stdout:
                output_parts.append(stdout.decode("utf-8", errors="replace"))
            if stderr:
                output_parts.append(f"STDERR:\n{stderr.decode('utf-8', errors='replace')}")

            output = "\n".join(output_parts) if output_parts else "(no output)"

            if len(output) > MAX_OUTPUT_CHARS:
                half = MAX_OUTPUT_CHARS // 2
                omitted = len(output) - MAX_OUTPUT_CHARS
                output = (
                    output[:half]
                    + f"\n\n... ({omitted} chars truncated) ...\n\n"
                    + output[-half:]
                )

            if proc.returncode != 0:
                output = f"Exit code: {proc.returncode}\n{output}"
                return ToolResult(output=output, is_error=True)

            return ToolResult(output=output)

        except asyncio.TimeoutError:
            return ToolResult(
                output=f"Command timed out after {timeout}s: {command}",
                is_error=True,
            )
