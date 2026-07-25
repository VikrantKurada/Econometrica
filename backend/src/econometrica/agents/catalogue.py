"""The tool registry, rendered for a model to read.

Agents select from the registry; they never invent a method. That only works
if the catalogue they are shown is complete and accurate, so this renders
straight from `get_registry()` rather than from a hand-kept list that would
drift the first time a tool is added.

Parameter schemas are included, not just names. A model that can name `garch`
but cannot see that `p` and `q` exist will guess at them, and a guessed
parameter is exactly the failure `PlanStep` has to reject afterwards — better
not to provoke it.
"""

import json
from typing import Any

from pydantic import BaseModel

from econometrica.econ import load_tools
from econometrica.econ.registry import get_registry

load_tools()

#: JSON Schema keys that carry no information a model needs and cost tokens
#: in proportion to the number of tools. `title` restates the field name;
#: `$defs`/`additionalProperties` describe validation, not intent.
_NOISE = frozenset({"title", "additionalProperties"})


def render_tool_catalogue(families: tuple[str, ...] | None = None) -> str:
    """Every tool a plan may name, grouped by family.

    ``families`` narrows the catalogue where a role only needs part of it —
    the whole registry is 36 tools, and a model asked to choose among all of
    them for a question about volatility chooses worse.
    """
    tools = sorted(get_registry().all(), key=lambda tool: (tool.family, tool.name))
    if families is not None:
        tools = [tool for tool in tools if tool.family in families]

    lines: list[str] = []
    current_family = ""
    for tool in tools:
        if tool.family != current_family:
            current_family = tool.family
            lines.append(f"\n## {current_family}")
        lines.append(f"\n### {tool.name} (v{tool.version})")
        lines.append(tool.summary)
        if tool.preconditions:
            lines.append("Preconditions: " + "; ".join(tool.preconditions))
        lines.append("Parameters: " + json.dumps(_parameters(tool.params_model), sort_keys=True))

    return "\n".join(lines).strip()


def _parameters(params_model: type[BaseModel]) -> dict[str, Any]:
    """The parameter schema, stripped of keys that only cost tokens."""
    schema = params_model.model_json_schema()
    properties: dict[str, Any] = schema.get("properties", {})
    rendered: dict[str, Any] = {
        name: {key: value for key, value in spec.items() if key not in _NOISE}
        for name, spec in properties.items()
    }
    # A nested model or enum leaves `$ref`s pointing into `$defs`; dropping
    # those would leave the model reading references to nothing.
    if "$defs" in schema:
        rendered["$defs"] = schema["$defs"]
    return rendered
