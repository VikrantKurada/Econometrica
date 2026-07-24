from dataclasses import dataclass

from econometrica.db.models import Chat, Project

# Mirrors the column defaults on Project. Project-level toggles are NOT NULL in
# the database, so None is only ever seen on an instance that has not been
# flushed yet — SQLAlchemy applies Python-side column defaults at INSERT time.
# Falling back to the column default keeps the resolver's answer identical
# before and after a flush, instead of leaking None into a bool field.
_PROJECT_TOGGLE_DEFAULT = False
_VALIDATION_TIER_DEFAULT = "critic"


def _project_toggle(value: bool | None) -> bool:
    return _PROJECT_TOGGLE_DEFAULT if value is None else value


@dataclass(frozen=True)
class ResolvedCapabilities:
    web_search: bool
    mcp: bool
    code_sandbox: bool
    validation_tier: str


def resolve_capabilities(project: Project, chat: Chat) -> ResolvedCapabilities:
    """Chat settings override project settings; None means inherit."""
    return ResolvedCapabilities(
        web_search=(
            _project_toggle(project.web_search_enabled)
            if chat.web_search_enabled is None
            else chat.web_search_enabled
        ),
        mcp=(
            _project_toggle(project.mcp_enabled)
            if chat.mcp_enabled is None
            else chat.mcp_enabled
        ),
        # The sandbox is deliberately not overridable per chat — it is the most
        # security-sensitive toggle in the system and stays at project scope.
        code_sandbox=_project_toggle(project.code_sandbox_enabled),
        validation_tier=(
            _VALIDATION_TIER_DEFAULT
            if project.validation_tier is None
            else project.validation_tier
        ),
    )
