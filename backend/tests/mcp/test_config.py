"""The typed MCP server config, parsed from the project's JSONB list."""

import pytest

from econometrica.mcp.config import McpServerConfig


def test_a_stdio_server_needs_a_command():
    with pytest.raises(ValueError, match="command"):
        McpServerConfig(name="files", transport="stdio")


def test_an_http_server_needs_a_url():
    with pytest.raises(ValueError, match="url"):
        McpServerConfig(name="wiki", transport="http")


def test_an_unknown_transport_is_rejected():
    with pytest.raises(ValueError):
        McpServerConfig(name="x", transport="carrier-pigeon", command="run")


def test_a_valid_stdio_config_round_trips_from_a_mapping():
    config = McpServerConfig.from_mapping(
        {"name": "files", "transport": "stdio", "command": "uvx", "args": ["files-mcp"]}
    )
    assert config.name == "files"
    assert config.command == "uvx"
    assert config.args == ["files-mcp"]


def test_a_valid_http_config_round_trips_from_a_mapping():
    config = McpServerConfig.from_mapping(
        {"name": "wiki", "transport": "http", "url": "https://mcp.example/api"}
    )
    assert config.transport == "http"
    assert config.url == "https://mcp.example/api"
