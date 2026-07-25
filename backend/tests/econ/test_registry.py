import pytest
from pydantic import BaseModel

from econometrica.econ.registry import ToolRegistry, get_registry


class DummyParams(BaseModel):
    window: int = 20


def test_registered_tool_is_retrievable_by_name():
    registry = ToolRegistry()

    @registry.register(
        name="dummy",
        version="1.0.0",
        params_model=DummyParams,
        family="test",
        summary="A dummy tool",
    )
    def dummy(data, params): ...

    tool = registry.get("dummy")
    assert tool.version == "1.0.0"
    assert tool.family == "test"


def test_registering_a_duplicate_name_raises():
    registry = ToolRegistry()

    @registry.register(
        name="dup", version="1.0.0", params_model=DummyParams, family="test", summary=""
    )
    def a(data, params): ...

    with pytest.raises(ValueError, match="already registered"):

        @registry.register(
            name="dup", version="1.0.0", params_model=DummyParams, family="test", summary=""
        )
        def b(data, params): ...


def test_unknown_tool_lookup_raises_keyerror():
    with pytest.raises(KeyError):
        ToolRegistry().get("nope")


def test_registry_emits_json_schema_for_llm_tool_calling():
    """Agents receive the registry as tool definitions; the schema must be complete."""
    registry = ToolRegistry()

    @registry.register(
        name="dummy",
        version="1.0.0",
        params_model=DummyParams,
        family="test",
        summary="A dummy tool",
    )
    def dummy(data, params): ...

    schemas = registry.to_tool_schemas()
    assert len(schemas) == 1
    assert schemas[0]["name"] == "dummy"
    assert schemas[0]["description"] == "A dummy tool"
    assert "window" in schemas[0]["input_schema"]["properties"]


@pytest.mark.phase_gate
def test_global_registry_contains_every_shipped_tool_family():
    families = {tool.family for tool in get_registry().all()}
    assert {"pricing", "efficiency", "volatility", "multivariate", "events"} <= families
