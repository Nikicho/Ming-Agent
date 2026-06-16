"""File read/write/edit tools."""

import os
from pathlib import Path
from typing import Any

from ming.tools.base import Tool, ToolResult

MAX_READ_CHARS = 100000


class FileReadTool(Tool):
    @property
    def name(self) -> str:
        return "file_read"

    @property
    def description(self) -> str:
        return "Read the contents of a file. Returns the file content with line numbers."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file to read."},
                "offset": {
                    "type": "integer",
                    "description": "Line number to start from (0-based). Default 0.",
                },
                "limit": {"type": "integer", "description": "Max lines to read. Default all."},
            },
            "required": ["path"],
        }

    def __init__(self, working_dir: str | None = None):
        self.working_dir = working_dir or os.getcwd()

    async def execute(
        self,
        path: str,
        offset: int = 0,
        limit: int | None = None,
        **_: Any,
    ) -> ToolResult:
        full_path = Path(self.working_dir) / path if not os.path.isabs(path) else Path(path)

        if not full_path.exists():
            return ToolResult(output=f"File not found: {full_path}", is_error=True)
        if not full_path.is_file():
            return ToolResult(output=f"Not a file: {full_path}", is_error=True)

        try:
            text = full_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return ToolResult(output=f"Error reading file: {e}", is_error=True)

        lines = text.splitlines()
        end = offset + limit if limit else len(lines)
        selected = lines[offset:end]

        numbered = "\n".join(f"{i + offset + 1}\t{line}" for i, line in enumerate(selected))
        if len(numbered) > MAX_READ_CHARS:
            numbered = numbered[:MAX_READ_CHARS] + "\n... (truncated)"

        header = f"File: {full_path} ({len(lines)} lines total)"
        if offset > 0 or limit:
            header += f", showing lines {offset + 1}-{min(end, len(lines))}"

        return ToolResult(output=f"{header}\n{numbered}")


class FileWriteTool(Tool):
    @property
    def name(self) -> str:
        return "file_write"

    @property
    def description(self) -> str:
        return (
            "Write content to a file. Creates the file if it doesn't exist, "
            "overwrites if it does."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to write to."},
                "content": {"type": "string", "description": "Content to write."},
            },
            "required": ["path", "content"],
        }

    def __init__(self, working_dir: str | None = None):
        self.working_dir = working_dir or os.getcwd()

    async def execute(self, path: str, content: str, **_: Any) -> ToolResult:
        full_path = Path(self.working_dir) / path if not os.path.isabs(path) else Path(path)

        try:
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding="utf-8")
            return ToolResult(output=f"Written {len(content)} chars to {full_path}")
        except Exception as e:
            return ToolResult(output=f"Error writing file: {e}", is_error=True)


class FileEditTool(Tool):
    @property
    def name(self) -> str:
        return "file_edit"

    @property
    def description(self) -> str:
        return (
            "Edit a file by replacing an exact string match. "
            "The old_string must match exactly (including whitespace/indentation)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file to edit."},
                "old_string": {
                    "type": "string",
                    "description": "Exact string to find and replace.",
                },
                "new_string": {"type": "string", "description": "Replacement string."},
            },
            "required": ["path", "old_string", "new_string"],
        }

    def __init__(self, working_dir: str | None = None):
        self.working_dir = working_dir or os.getcwd()

    async def execute(self, path: str, old_string: str, new_string: str, **_: Any) -> ToolResult:
        full_path = Path(self.working_dir) / path if not os.path.isabs(path) else Path(path)

        if not full_path.exists():
            return ToolResult(output=f"File not found: {full_path}", is_error=True)

        try:
            text = full_path.read_text(encoding="utf-8")
        except Exception as e:
            return ToolResult(output=f"Error reading file: {e}", is_error=True)

        count = text.count(old_string)
        if count == 0:
            return ToolResult(output="old_string not found in file.", is_error=True)
        if count > 1:
            return ToolResult(
                output=f"old_string found {count} times. Must be unique. Provide more context.",
                is_error=True,
            )

        new_text = text.replace(old_string, new_string, 1)
        full_path.write_text(new_text, encoding="utf-8")
        return ToolResult(output=f"Edited {full_path}: replaced 1 occurrence.")
