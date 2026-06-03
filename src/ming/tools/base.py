"""Tool base class and registry."""

import json
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel


class ToolResult(BaseModel):
    """Result from executing a tool."""

    output: str
    is_error: bool = False


class Tool(ABC):
    """Base class for all Ming tools."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name used in function calling."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Description shown to the LLM."""
        ...

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        """JSON Schema for the tool's parameters."""
        ...

    @abstractmethod
    async def execute(self, **kwargs: Any) -> ToolResult:
        """Execute the tool with the given parameters."""
        ...

    def to_openai_schema(self) -> dict[str, Any]:
        """Convert to OpenAI function calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """Registry of available tools."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all_schemas(self) -> list[dict[str, Any]]:
        return [t.to_openai_schema() for t in self._tools.values()]

    async def execute(self, name: str, arguments: str) -> ToolResult:
        """Execute a tool by name with JSON arguments string."""
        tool = self._tools.get(name)
        if not tool:
            return ToolResult(output=f"Unknown tool: {name}", is_error=True)
        try:
            kwargs = json.loads(arguments) if arguments else {}
        except json.JSONDecodeError as e:
            return ToolResult(output=f"Invalid JSON arguments: {e}", is_error=True)
        try:
            return await tool.execute(**kwargs)
        except Exception as e:
            return ToolResult(output=f"Tool execution error: {type(e).__name__}: {e}", is_error=True)
