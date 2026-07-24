from econometrica.db.models import Chat, Project
from econometrica.services.capabilities import resolve_capabilities


def test_chat_inherits_project_settings_when_unset():
    project = Project(name="P", web_search_enabled=True, mcp_enabled=False)
    chat = Chat(name="C", web_search_enabled=None, mcp_enabled=None)
    resolved = resolve_capabilities(project, chat)
    assert resolved.web_search is True
    assert resolved.mcp is False


def test_chat_override_beats_project_setting():
    project = Project(name="P", web_search_enabled=True, mcp_enabled=True)
    chat = Chat(name="C", web_search_enabled=False, mcp_enabled=None)
    resolved = resolve_capabilities(project, chat)
    assert resolved.web_search is False
    assert resolved.mcp is True


def test_code_sandbox_is_project_level_only_and_defaults_off():
    project = Project(name="P")
    chat = Chat(name="C")
    resolved = resolve_capabilities(project, chat)
    assert resolved.code_sandbox is False
