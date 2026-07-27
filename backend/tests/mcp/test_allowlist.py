"""The gate every MCP tool call passes through.

This is the security surface of the whole feature, so the tests are about what
is *refused* rather than what works. Three properties do the work:

* **Default deny.** An empty allowlist allows nothing. A project that turned MCP
  on and listed nothing has not thereby consented to everything a server offers.
* **Explicit, not patterned.** §9 says tools "pass through an explicit
  allowlist"; a `server:*` wildcard would quietly re-admit whatever a server
  added next, which is the failure this exists to prevent.
* **Naming a server is not naming its tools.** Connecting to a server is how you
  find out what it offers; it is not agreement to run any of it.
"""

import pytest

from econometrica.mcp.allowlist import Allowlist, ToolNotAllowedError, ToolRef


def ref(server: str = "files", tool: str = "read") -> ToolRef:
    return ToolRef(server=server, tool=tool)


# --- what it allows -----------------------------------------------------------


def test_a_listed_tool_is_allowed():
    assert Allowlist(["files:read"]).allows(ref()) is True


def test_require_returns_quietly_for_a_listed_tool():
    Allowlist(["files:read"]).require(ref())


def test_several_tools_can_be_listed():
    allowed = Allowlist(["files:read", "files:write", "search:query"])

    assert allowed.allows(ref("files", "write")) is True
    assert allowed.allows(ref("search", "query")) is True


# --- what it refuses ----------------------------------------------------------


def test_an_unlisted_tool_is_refused():
    with pytest.raises(ToolNotAllowedError, match="files:delete"):
        Allowlist(["files:read"]).require(ref("files", "delete"))


def test_an_empty_allowlist_allows_nothing():
    """Default deny. Turning MCP on is not consent to whatever a server offers."""
    empty = Allowlist([])

    assert empty.allows(ref()) is False
    with pytest.raises(ToolNotAllowedError):
        empty.require(ref())


def test_naming_a_server_does_not_allow_its_tools():
    """Connecting is how you discover what a server offers. It is not agreement
    to run any of it."""
    with pytest.raises(ToolNotAllowedError):
        Allowlist(["files"]).require(ref("files", "read"))


def test_a_wildcard_is_not_a_pattern():
    """§9 asks for an *explicit* allowlist. A `server:*` entry would re-admit
    whatever the server added next, which is the whole failure this prevents —
    so it is a literal tool name and matches only a tool actually called `*`."""
    allowed = Allowlist(["files:*"])

    assert allowed.allows(ref("files", "read")) is False
    assert allowed.allows(ref("files", "delete")) is False


def test_a_tool_from_another_server_with_the_same_name_is_refused():
    """`files:read` and `shell:read` are different tools. Matching on the tool
    name alone would let a second server impersonate a trusted one."""
    with pytest.raises(ToolNotAllowedError):
        Allowlist(["files:read"]).require(ref("shell", "read"))


def test_matching_is_exact_not_case_insensitive():
    """A server chooses its own tool names, and treating `Read` as `read` would
    make the gate depend on a normalisation the server never agreed to."""
    assert Allowlist(["files:read"]).allows(ref("files", "Read")) is False


def test_a_malformed_entry_is_ignored_rather_than_allowing_everything():
    """An entry with no separator names no tool. Failing open here would be the
    worst possible reading of a typo."""
    allowed = Allowlist(["nonsense", "", "files:read"])

    assert allowed.allows(ref("files", "read")) is True
    assert allowed.allows(ref("nonsense", "anything")) is False


# --- how it is described ------------------------------------------------------


def test_the_refusal_names_the_tool_and_what_is_allowed():
    """A user has to be able to fix this, and the fix is one entry."""
    with pytest.raises(ToolNotAllowedError) as raised:
        Allowlist(["files:read"]).require(ref("files", "delete"))

    message = str(raised.value)
    assert "files:delete" in message
    assert "files:read" in message


def test_the_refusal_of_an_empty_allowlist_says_so():
    with pytest.raises(ToolNotAllowedError, match="no MCP tools"):
        Allowlist([]).require(ref())


def test_entries_round_trip_as_written():
    assert Allowlist(["files:read"]).entries == ("files:read",)


def test_a_reference_renders_as_its_entry():
    assert str(ref("files", "read")) == "files:read"


def test_a_reference_parses_from_its_entry():
    assert ToolRef.parse("files:read") == ref("files", "read")


def test_a_reference_with_no_separator_will_not_parse():
    assert ToolRef.parse("files") is None


def test_a_tool_name_may_contain_a_colon():
    """Only the first separator splits, so a server namespacing its own tools
    does not become unaddressable."""
    parsed = ToolRef.parse("files:fs:read")

    assert parsed == ToolRef(server="files", tool="fs:read")


# --- where it comes from ------------------------------------------------------


def test_a_project_supplies_its_own_allowlist():
    from econometrica.db.models import Project

    project = Project(name="P", mcp_allowlist=["files:read"])

    assert Allowlist.for_project(project).allows(ref("files", "read")) is True


def test_a_project_that_has_configured_nothing_allows_nothing():
    """The default has to be deny, not absent-means-everything."""
    from econometrica.db.models import Project

    assert len(Allowlist.for_project(Project(name="P"))) == 0
