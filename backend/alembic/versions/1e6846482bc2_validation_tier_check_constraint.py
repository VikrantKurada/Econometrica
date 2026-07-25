"""validation tier check constraint

Revision ID: 1e6846482bc2
Revises: 4e655f9893f9
Create Date: 2026-07-25 12:18:18.506903

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1e6846482bc2"
down_revision: str | Sequence[str] | None = "4e655f9893f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Constrain projects.validation_tier to the three known tiers.

    Hand-written: Alembic's autogenerate does not detect CHECK constraints, so
    it produced an empty revision here. For the same reason ``alembic check``
    cannot verify this constraint — the test suite is its gate.

    Any pre-existing row carrying an unknown tier is normalised to 'critic'
    (the column default) first, so the constraint can be applied to a database
    that already drifted rather than failing the migration.
    """
    op.execute(
        "UPDATE projects SET validation_tier = 'critic' "
        "WHERE validation_tier NOT IN ('single', 'critic', 'consensus')"
    )
    op.create_check_constraint(
        "ck_projects_validation_tier_known",
        "projects",
        "validation_tier IN ('single', 'critic', 'consensus')",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "ck_projects_validation_tier_known", "projects", type_="check"
    )
