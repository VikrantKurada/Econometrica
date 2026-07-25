"""Enforcing a tool's declared preconditions against real data.

The registry's ``preconditions`` are prose: they tell a model what a tool
expects, and a model may misread them. A ``Gate`` is the same knowledge in a
form that cannot be misread, evaluated here against the series a step actually
names.

Three verdicts, not two. **Unjudged is not refused** — that is the tri-state
`Diagnostic.passed` invariant reaching one layer up. A check that could not
run (too few observations, a degenerate series) must not silently become a
refusal, and must not silently become an approval either: it travels to the
Validator as an unjudged verdict, and the Narrator has to disclose it.
"""

import numpy as np
import pandas as pd
from pydantic import BaseModel

from econometrica.econ.diagnostics.engine import run_diagnostics
from econometrica.econ.registry import Gate, RegisteredTool, get_registry


class PreconditionVerdict(BaseModel):
    """Whether one gate lets one step run."""

    tool: str
    check: str
    #: False only when the check ran and disagreed with the gate.
    allowed: bool
    #: False when the check could not be evaluated at all.
    judged: bool
    detail: str

    @property
    def refused(self) -> bool:
        return self.judged and not self.allowed


def evaluate_gates(
    tool: RegisteredTool, series: dict[str, pd.Series]
) -> list[PreconditionVerdict]:
    """Check every gate on ``tool`` against every series the step names.

    All named columns must satisfy a gate: a VAR is not fit on "mostly
    stationary" data, and one non-stationary series is enough to make the
    whole system's dynamics spurious.
    """
    verdicts: list[PreconditionVerdict] = []
    for gate in tool.gates:
        for name, values in series.items():
            verdicts.append(_evaluate(tool, gate, name, values))
    return verdicts


def _evaluate(
    tool: RegisteredTool, gate: Gate, column: str, values: pd.Series
) -> PreconditionVerdict:
    try:
        found = _CHECKS[gate.check](values)
    except Exception as exc:
        # Deliberately broad. Any reason a check cannot run — too few
        # observations, a constant series, a solver that would not converge —
        # is a reason to withhold judgement, never to refuse.
        return PreconditionVerdict(
            tool=tool.name,
            check=gate.check,
            allowed=True,
            judged=False,
            detail=f"{gate.check} could not be evaluated on {column!r}: {exc}",
        )

    if found is None:
        return PreconditionVerdict(
            tool=tool.name,
            check=gate.check,
            allowed=True,
            judged=False,
            detail=f"{gate.check} returned no verdict on {column!r}",
        )

    allowed = found is gate.expect
    wanted = "present" if gate.expect else "absent"
    actual = "present" if found else "absent"
    detail = f"{gate.check} on {column!r}: wanted {wanted}, found {actual}"
    if not allowed and gate.because:
        detail = f"{detail} — {gate.because}"

    return PreconditionVerdict(
        tool=tool.name, check=gate.check, allowed=allowed, judged=True, detail=detail
    )


def _arch_effects(values: pd.Series) -> bool | None:
    """Whether conditional heteroskedasticity is present.

    ARCH-LM reads fail-to-reject-as-good: ``passed=True`` means the *absence*
    of ARCH effects survived the test. GARCH wants the opposite, so the sense
    is inverted here rather than at each call site.
    """
    for diagnostic in run_diagnostics(_prewhiten(values), checks=["arch_lm"]):
        if diagnostic.name == "arch_lm":
            return None if diagnostic.passed is None else not diagnostic.passed
    return None


def _prewhiten(values: pd.Series) -> pd.Series:
    """Strip first-order mean dynamics before testing the variance.

    ARCH-LM regresses squared values on their own lags, so a series
    autocorrelated *in the mean* fails it whether or not its variance moves:
    an AR(1) with phi=0.5 and perfectly homoskedastic innovations reports ARCH
    effects, and a gate reading that would wave through exactly the GARCH fit
    it exists to refuse.

    The diagnostics engine is documented as running over a fitted model's
    residuals. A gate handed a raw series therefore has to supply the fit —
    an AR(1) here, which is what removes the term that causes the false
    positive. Plain least squares rather than statsmodels: two coefficients,
    and the result has to be bit-identical between runs.
    """
    series = values.astype(float).dropna()
    frame = pd.concat({"y": series, "lag": series.shift(1)}, axis=1).dropna()
    if len(frame) < 3:
        # Too short for the engine to judge anyway; let it say so.
        return series

    design = np.column_stack([np.ones(len(frame)), frame["lag"].to_numpy()])
    coefficients, *_ = np.linalg.lstsq(design, frame["y"].to_numpy(), rcond=None)
    return pd.Series(frame["y"].to_numpy() - design @ coefficients, index=frame.index)


def _stationarity(values: pd.Series) -> bool | None:
    """Whether the series looks stationary, per the registry's own ADF tool.

    Run through the registry rather than calling `arch` directly so the gate
    and the plan step that a user can read in the trace are answering with the
    same implementation, lag rule and version.
    """
    tool = get_registry().get("adf")
    frame = pd.DataFrame({"series": values})
    result = tool.fn(frame, tool.params_model.model_validate({"column": "series"}))
    for diagnostic in result.diagnostics:
        if diagnostic.name == "adf":
            return diagnostic.passed
    return None


_CHECKS = {"arch_effects": _arch_effects, "stationarity": _stationarity}
