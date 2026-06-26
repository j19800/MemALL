from dataclasses import dataclass, field
from typing import Callable


@dataclass
class ToolDef:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], str]
    annotations: dict = field(default_factory=dict)
    intercept_category: str | None = None


class ToolRegistry:
    _tools: dict[str, ToolDef] = {}

    @classmethod
    def register(cls, tool: ToolDef):
        cls._tools[tool.name] = tool

    @classmethod
    def get(cls, name: str) -> ToolDef | None:
        return cls._tools.get(name)

    @classmethod
    def dispatch(cls, name: str, arguments: dict) -> str:
        tool = cls._tools.get(name)
        if not tool:
            raise ValueError(f"unknown tool: {name}")
        return tool.handler(arguments)

    @classmethod
    def list_definitions(cls) -> list[dict]:
        result = []
        for t in cls._tools.values():
            entry = {
                "name": t.name,
                "description": t.description,
                "inputSchema": t.input_schema,
            }
            if t.annotations:
                entry["annotations"] = t.annotations
            result.append(entry)
        return result


registry = ToolRegistry()
