"""allow researcher as a run step agent

Revision ID: a7b3c9d1e2f4
Revises: f0a1c2d3e4b5
Create Date: 2026-08-01 09:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7b3c9d1e2f4"
down_revision: str | Sequence[str] | None = "f0a1c2d3e4b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AGENTS = (
    "planner",
    "data_steward",
    "econometrician",
    "validator",
    "narrator",
    "quant_coder",
    "query_writer",
    "researcher",
)
_PREVIOUS = _AGENTS[:-1]


def _in_list(values: Sequence[str]) -> str:
    return "agent IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    """Add `researcher` to the agents a step may name.

    Hand-written for the reason CLAUDE.md records: autogenerate emits CHECK
    constraints only when it *creates* a table and sees nothing when one changes
    on a table that already exists, so this revision came out empty. `alembic
    check` cannot see it either — the gate is `tests/db/test_run_model.py`, which
    inserts a `researcher` step against the real database, plus the value test in
    `tests/db/test_migrations.py`.

    Dropped and recreated rather than altered: Postgres has no `ALTER
    CONSTRAINT` for a CHECK expression.
    """
    op.drop_constraint("ck_run_steps_agent_known", "run_steps", type_="check")
    op.create_check_constraint("ck_run_steps_agent_known", "run_steps", _in_list(_AGENTS))


def downgrade() -> None:
    """Remove the researcher steps first.

    Narrowing the constraint over rows that already violate it would fail the
    migration outright. The rows are deleted because there is nowhere honest to
    move them — an MCP tool call re-labelled `econometrician` would claim a
    registry tool ran.
    """
    op.execute("DELETE FROM run_steps WHERE agent = 'researcher'")
    op.drop_constraint("ck_run_steps_agent_known", "run_steps", type_="check")
    op.create_check_constraint("ck_run_steps_agent_known", "run_steps", _in_list(_PREVIOUS))
