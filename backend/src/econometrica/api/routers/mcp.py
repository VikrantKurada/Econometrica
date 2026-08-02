"""Discovering a project's MCP tools. Listing is how a user builds the
allowlist — and showing a tool never makes it callable."""

from uuid import UUID

from fastapi import APIRouter

from econometrica.api.deps import SessionDep, get_project_or_404
from econometrica.mcp.allowlist import Allowlist
from econometrica.mcp.config import McpServerConfig
from econometrica.mcp.connect import connect_server
from econometrica.schemas.mcp import McpServerToolsRead, McpToolRead

router = APIRouter(prefix="/api/projects", tags=["mcp"])


@router.get("/{project_id}/mcp/tools", response_model=list[McpServerToolsRead])
async def discover_tools(project_id: UUID, session: SessionDep) -> list[McpServerToolsRead]:
    project = await get_project_or_404(session, project_id)
    allowlist = Allowlist.for_project(project)

    results: list[McpServerToolsRead] = []
    for entry in project.mcp_servers or []:
        try:
            config = McpServerConfig.from_mapping(entry)
        except (ValueError, TypeError) as exc:
            results.append(
                McpServerToolsRead(
                    server=str(entry.get("name", "?")) if isinstance(entry, dict) else "?",
                    tools=[],
                    error=str(exc),
                )
            )
            continue
        try:
            async with connect_server(config, allowlist) as client:
                discovered = await client.discover()
        except Exception as exc:
            results.append(McpServerToolsRead(server=config.name, tools=[], error=str(exc)))
            continue
        results.append(
            McpServerToolsRead(
                server=config.name,
                tools=[
                    McpToolRead(
                        name=tool.name,
                        description=tool.description,
                        input_schema=tool.input_schema,
                        allowed=tool.allowed,
                    )
                    for tool in discovered
                ],
            )
        )
    return results
