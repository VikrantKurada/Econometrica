"""allow query_writer as a run step agent

Revision ID: f0a1c2d3e4b5
Revises: b3a17c0d9e42
Create Date: 2026-07-31 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f0a1c2d3e4b5"
down_revision: str | Sequence[str] | None = "b3a17c0d9e42"
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
)
_PREVIOUS = _AGENTS[:-1]


def _in_list(values: Sequence[str]) -> str:
    return "agent IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    """Add `query_writer` to the agents a step may name.

    Hand-written for the reason CLAUDE.md records: autogenerate emits CHECK
    constraints only when it *creates* a table and sees nothing when one changes
    on a table that already exists, so this revision came out empty. `alembic
    check` cannot see it either — the gate is `tests/db/test_run_model.py`, which
    inserts a `query_writer` step against the real database, plus the value test
    in `tests/db/test_migrations.py`.

    Dropped and recreated rather than altered: Postgres has no `ALTER
    CONSTRAINT` for a CHECK expression.
    """
    op.drop_constraint("ck_run_steps_agent_known", "run_steps", type_="check")
    op.create_check_constraint("ck_run_steps_agent_known", "run_steps", _in_list(_AGENTS))


def downgrade() -> None:
    """Remove the query_writer steps first.

    Narrowing the constraint over rows that already violate it would fail the
    migration outright. The rows are deleted because there is nowhere honest to
    move them.
    """
    op.execute("DELETE FROM run_steps WHERE agent = 'query_writer'")
    op.drop_constraint("ck_run_steps_agent_known", "run_steps", type_="check")
    op.create_check_constraint("ck_run_steps_agent_known", "run_steps", _in_list(_PREVIOUS))
