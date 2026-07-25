"""Drift between the models and the migrations, where nothing else looks."""

from pathlib import Path

from sqlalchemy import CheckConstraint

import econometrica.db.models  # noqa: F401 — registers every mapper
from econometrica.db.base import Base

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"


def test_every_check_constraint_reaches_a_migration():
    """A constraint in the models and in no migration exists only in tests.

    Autogenerate *does* emit CHECK constraints when it creates a table — the
    runs/run_steps revision carries all thirteen. What it cannot see is one
    added to or changed on a table that already exists, which is why the
    `validation_tier` revision had to be written by hand and came out empty
    otherwise. `alembic check` cannot see either case, so this is the gate.
    """
    declared = {
        constraint.name
        for table in Base.metadata.tables.values()
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name
    }
    assert declared, "expected the models to declare check constraints"

    migrations = "\n".join(
        path.read_text(encoding="utf-8") for path in VERSIONS.glob("*.py")
    )
    missing = sorted(name for name in declared if name not in migrations)

    assert missing == [], (
        f"these constraints exist in the models but in no migration: {missing}."
        " A fresh database would not have them."
    )
