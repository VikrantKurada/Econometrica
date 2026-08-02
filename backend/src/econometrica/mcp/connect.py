"""Building live MCP sessions from a project's server configs.

The MCP SDK's transport types stop here, the way a vendor SDK's stop in
`llm/providers/`. Above this, everything speaks `McpClient`. A server is
connected only for the research phase and closed after — it is not a resident
dependency of the app.

A stdio server is an arbitrary local command spawned with host privileges (see
the design note's trust model); this module spawns it, it does not sandbox it.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from typing import Protocol

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

from econometrica.mcp.allowlist import Allowlist
from econometrica.mcp.client import McpClient
from econometrica.mcp.config import McpServerConfig

logger = logging.getLogger(__name__)


@asynccontextmanager
async def connect_server(
    config: McpServerConfig, allowlist: Allowlist
) -> AsyncIterator[McpClient]:
    """A connected, allowlist-gated client for one server."""
    async with AsyncExitStack() as stack:
        if config.transport == "stdio":
            params = StdioServerParameters(
                command=config.command, args=config.args, env=config.env or None
            )
            streams = await stack.enter_async_context(stdio_client(params))
        else:
            streams = await stack.enter_async_context(
                streamablehttp_client(config.url, headers=config.headers or None)
            )
        # stdio yields (read, write); http yields (read, write, get_session_id).
        read, write = streams[0], streams[1]
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        yield McpClient(session=session, server=config.name, allowlist=allowlist)


class Connector(Protocol):
    """What the Researcher sees: a way to open its tools and close them after."""

    def open(self) -> AbstractAsyncContextManager[list[McpClient]]:
        ...


class McpConnector:
    """Opens every server a project configured, degrading past the ones it can't."""

    def __init__(self, configs: list[McpServerConfig], allowlist: Allowlist) -> None:
        self._configs = configs
        self._allowlist = allowlist

    @asynccontextmanager
    async def open(self) -> AsyncIterator[list[McpClient]]:
        async with AsyncExitStack() as stack:
            clients: list[McpClient] = []
            for config in self._configs:
                try:
                    client = await stack.enter_async_context(
                        connect_server(config, self._allowlist)
                    )
                except Exception as exc:
                    # An unreachable server is a finding, not a failure: the run
                    # proceeds with the tools it could reach.
                    logger.warning(
                        "mcp server %s could not be connected: %s", config.name, exc
                    )
                    continue
                clients.append(client)
            yield clients
