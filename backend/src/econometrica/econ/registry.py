"""Versioned tool registry.

LLM agents never compute statistics — they select tools from this registry.
The registry renders itself as LLM tool definitions via :meth:`to_tool_schemas`.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel

from econometrica.econ.types import ResultSet

ToolFn = Callable[[pd.DataFrame, BaseModel], ResultSet]

#: Deterministic properties of the input data that a gate can demand. Kept
#: deliberately short: every entry needs an implementation in `econ.gates`,
#: and a vocabulary nobody can enumerate is one nobody can enforce.
GateCheck = Literal["arch_effects", "stationarity"]


@dataclass(frozen=True)
class Gate:
    """A precondition on the input data, enforced before a tool may run.

    ``preconditions`` next door is prose, written for the model to read while
    it plans. This is the refusal. Both exist because guidance and enforcement
    are different jobs — and only one of them can be argued with.

    ``expect`` carries real weight rather than being a formality: a VAR needs
    stationarity present and a VECM needs it absent, so the two are the same
    gate with opposite expectations.
    """

    check: GateCheck
    expect: bool = True
    #: Shown to the user when the gate refuses, so a refusal teaches something.
    because: str = ""


@dataclass(frozen=True)
class RegisteredTool:
    name: str
    version: str
    family: str
    summary: str
    params_model: type[BaseModel]
    fn: ToolFn
    preconditions: tuple[str, ...] = ()
    gates: tuple[Gate, ...] = ()


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
        gates: tuple[Gate, ...] = (),
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
                gates=gates,
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
