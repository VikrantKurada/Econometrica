"""Versioned tool registry.

LLM agents never compute statistics — they select tools from this registry.
The registry renders itself as LLM tool definitions via :meth:`to_tool_schemas`.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pandas as pd
from pydantic import BaseModel

from econometrica.econ.types import ResultSet

ToolFn = Callable[[pd.DataFrame, BaseModel], ResultSet]


@dataclass(frozen=True)
class RegisteredTool:
    name: str
    version: str
    family: str
    summary: str
    params_model: type[BaseModel]
    fn: ToolFn
    preconditions: tuple[str, ...] = ()


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(
        self,
        *,
        name: str,
        version: str,
        family: str,
        summary: str,
        params_model: type[BaseModel],
        preconditions: tuple[str, ...] = (),
    ) -> Callable[[ToolFn], ToolFn]:
        def decorator(fn: ToolFn) -> ToolFn:
            if name in self._tools:
                raise ValueError(f"tool {name!r} is already registered")
            self._tools[name] = RegisteredTool(
                name=name,
                version=version,
                family=family,
                summary=summary,
                params_model=params_model,
                fn=fn,
                preconditions=preconditions,
            )
            return fn

        return decorator

    def get(self, name: str) -> RegisteredTool:
        if name not in self._tools:
            raise KeyError(f"unknown tool: {name!r}")
        return self._tools[name]

    def all(self) -> list[RegisteredTool]:
        return list(self._tools.values())

    def to_tool_schemas(self) -> list[dict[str, Any]]:
        """Render the registry as LLM tool definitions."""
        return [
            {
                "name": tool.name,
                "description": tool.summary,
                "input_schema": tool.params_model.model_json_schema(),
            }
            for tool in self._tools.values()
        ]


_REGISTRY = ToolRegistry()


def get_registry() -> ToolRegistry:
    return _REGISTRY
