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


def test_every_value_in_a_check_constraint_vocabulary_reaches_a_migration():
    """Names are not enough — a widened list is drift the name test cannot see.

    `ck_run_steps_agent_known` was in the initial revision from the start, so
    adding `quant_coder` to `STEP_AGENTS` left the name test green and a fresh
    database rejecting every sandbox step. Autogenerate does not see a CHECK
    changed on an existing table, and `alembic check` does not look, so the
    values themselves have to be asserted.
    """
    from econometrica.db.models.run import (
        RUN_STATUSES,
        RUN_TIERS,
        STEP_AGENTS,
        STEP_KINDS,
        STEP_STATUSES,
    )

    migrations = "\n".join(path.read_text(encoding="utf-8") for path in VERSIONS.glob("*.py"))
    vocabularies = {
        "RUN_STATUSES": RUN_STATUSES,
        "RUN_TIERS": RUN_TIERS,
        "STEP_AGENTS": STEP_AGENTS,
        "STEP_KINDS": STEP_KINDS,
        "STEP_STATUSES": STEP_STATUSES,
    }
    missing = {
        name: [value for value in values if f"'{value}'" not in migrations]
        for name, values in vocabularies.items()
    }

    assert {name: gaps for name, gaps in missing.items() if gaps} == {}
