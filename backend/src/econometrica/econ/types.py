"""Result types for the econometrics core.

Every downstream consumer — agents, API, charts, exports — depends only on
:class:`ResultSet`. Library result objects (statsmodels, arch, linearmodels)
must never leak past the tool boundary.
"""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class Estimate(BaseModel):
    """A single estimated coefficient with its inferential statistics."""

    name: str
    value: float
    std_error: float | None = None
    t_stat: float | None = None
    p_value: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None

    def is_significant(self, alpha: float = 0.05) -> bool:
        return self.p_value is not None and self.p_value < alpha


class Diagnostic(BaseModel):
    """A deterministic assumption check. ``passed`` is set by the tool, never inferred."""

    name: str
    statistic: float
    p_value: float | None = None
    critical_values: dict[str, float] = Field(default_factory=dict)
    passed: bool | None = None
    interpretation: str = ""


class Table(BaseModel):
    columns: list[str]
    rows: list[list[Any]]


class Series(BaseModel):
    name: str
    x: list[Any]
    y: list[float | None]


class Manifest(BaseModel):
    """Everything needed to reproduce a result bit-for-bit."""

    data_fingerprint: str
    tool: str
    tool_version: str
    params_hash: str = ""
    library_versions: dict[str, str] = Field(default_factory=dict)
    seed: int | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ResultSet(BaseModel):
    tool: str
    version: str
    params: dict[str, Any]
    estimates: list[Estimate] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    scalars: dict[str, float] = Field(default_factory=dict)
    tables: dict[str, Table] = Field(default_factory=dict)
    series: dict[str, Series] = Field(default_factory=dict)
    manifest: Manifest

    def estimate(self, name: str) -> Estimate | None:
        return next((e for e in self.estimates if e.name == name), None)

    def all_numeric_values(self) -> set[float]:
        """Every number a narrator is permitted to cite."""
        values: set[float] = set(self.scalars.values())
        for est in self.estimates:
            for field in (
                est.value,
                est.std_error,
                est.t_stat,
                est.p_value,
                est.ci_low,
                est.ci_high,
            ):
                if field is not None:
                    values.add(field)
        for diag in self.diagnostics:
            values.add(diag.statistic)
            if diag.p_value is not None:
                values.add(diag.p_value)
            values.update(diag.critical_values.values())
        return values
