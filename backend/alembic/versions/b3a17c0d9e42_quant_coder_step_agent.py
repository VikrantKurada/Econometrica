"""allow quant_coder as a run step agent

Revision ID: b3a17c0d9e42
Revises: 7441efabf4a6
Create Date: 2026-07-27 18:40:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3a17c0d9e42"
down_revision: str | Sequence[str] | None = "7441efabf4a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AGENTS = ("planner", "data_steward", "econometrician", "validator", "narrator", "quant_coder")
_PREVIOUS = _AGENTS[:-1]


def _in_list(values: Sequence[str]) -> str:
    return "agent IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    """Add `quant_coder` to the agents a step may name.

    Hand-written, and the reason is the one CLAUDE.md records: autogenerate
    emits CHECK constraints when it *creates* a table, and sees nothing at all
    when one changes on a table that already exists. This revision came out
    empty. ``alembic check`` cannot see it either, so the only gate is
    ``tests/db/test_run_model.py``, which inserts a `quant_coder` step against
    the real database.

    Dropped and recreated rather than altered: Postgres has no
    ``ALTER CONSTRAINT`` for a CHECK expression.
    """
    op.drop_constraint("ck_run_steps_agent_known", "run_steps", type_="check")
    op.create_check_constraint("ck_run_steps_agent_known", "run_steps", _in_list(_AGENTS))


def downgrade() -> None:
    """Remove the quant_coder steps first.

    Narrowing the constraint over rows that already violate it would fail the
    migration outright, and a downgrade that cannot run is not a downgrade. The
    rows are deleted because there is nowhere honest to move them: a sandbox
    step re-labelled `econometrician` would claim a registry tool ran.
    """
    op.execute("DELETE FROM run_steps WHERE agent = 'quant_coder'")
    op.drop_constraint("ck_run_steps_agent_known", "run_steps", type_="check")
    op.create_check_constraint("ck_run_steps_agent_known", "run_steps", _in_list(_PREVIOUS))
