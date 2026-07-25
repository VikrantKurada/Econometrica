"""GARCH-family conditional volatility models: GARCH, EGARCH and GJR-GARCH.

One shared implementation over ``arch.arch_model``; the three registered
tools differ only in the volatility specification, its parameter set and the
model-appropriate persistence formula.

Scale convention (documented once here, referenced everywhere): ``arch``
recommends estimating on percentage returns (x100) for optimizer stability,
so the tools rescale internally. Parameter estimates are reported on the
PERCENTAGE-return scale exactly as fitted — mu in percent per period, omega
in percent^2 (or log-percent^2 for EGARCH) — because EGARCH's omega lives on
the log-variance scale where undoing the x100 is affine in beta and would
need delta-method standard errors; a single uniform convention beats a
per-model patchwork. The shape parameters (alpha, beta, gamma, nu, eta,
lambda) and everything derived from them (persistence, half_life) are
scale-free. Everything in return units is rescaled back to DECIMALS: the
``conditional_volatility`` series and the ``unconditional_vol`` scalars.
Log-likelihood/AIC/BIC are on the percentage scale — comparable across fits
of the same series, as in the t-vs-normal comparison.

Persistence by model:

- GARCH(p,q): sum(alpha) + sum(beta) — the decay rate of variance shocks.
- GJR-GARCH(p,o,q): sum(alpha) + 0.5 * sum(gamma) + sum(beta); the half
  weight is E[I(eps<0)] = 1/2 under symmetric innovations (the standard
  convention, kept for skewt fits too and noted in the interpretation).
- EGARCH(p,o,q): sum(beta) only — persistence of the log-variance AR; the
  alpha and gamma terms are mean-zero shocks to log-variance.

Convergence handling: a fit whose parameters, standard errors or
log-likelihood are non-finite is unusable and raises ValueError (e.g. a
constant series). A fit that is numerically usable but whose optimizer flag
is nonzero is returned with the ``convergence`` diagnostic set to
``passed=False`` — reported, not hidden, and not fatal.
"""

import math
import warnings
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from econometrica.econ._common import (
    TRANSFORM_FIELD_DOC,
    Transform,
    build_manifest,
    coerce_params,
    prepare_series,
    series_from,
)
from econometrica.econ.registry import Gate, get_registry
from econometrica.econ.returns import PERIODS_PER_YEAR
from econometrica.econ.types import Diagnostic, Estimate, ResultSet

_VERSION = "1.0.0"
_LIBRARIES = ("arch", "numpy", "pandas", "statsmodels")

_SCALE_NOTE = (
    " Parameter estimates are on the percentage-return scale (returns x100,"
    " arch's recommended scaling); alpha/beta/gamma/nu and persistence are"
    " scale-free, while the conditional_volatility series and the"
    " unconditional_vol scalars are reported in decimal return units."
)


class _GarchFamilyParams(BaseModel):
    """Fields shared by the three GARCH-family tools."""

    column: str = Field(
        default="return", description="Column holding per-period returns in DECIMAL units."
    )
    transform: Transform = Field(default="none", description=TRANSFORM_FIELD_DOC)
    dist: Literal["normal", "t", "skewt"] = Field(
        default="normal",
        description="Innovation distribution: 'normal', Student's 't' (adds nu),"
        " or Hansen's 'skewt' (adds eta and lambda).",
    )
    mean: Literal["constant", "zero"] = Field(
        default="constant",
        description="Mean specification: 'constant' estimates mu, 'zero' fixes it at 0.",
    )
    frequency: Literal["D", "W", "M", "Q", "A"] = Field(
        default="D", description="Return frequency, used to annualise the unconditional vol."
    )
    lb_lags: int = Field(
        default=10,
        ge=1,
        description="Ljung-Box lag for the standardized-residual diagnostics.",
    )
    min_obs: int = Field(
        default=250,
        ge=100,
        description="Minimum observations required after transforming; GARCH"
        " likelihoods are unreliable on short samples.",
    )


class GarchParams(_GarchFamilyParams):
    """Options for the symmetric GARCH(p, q) model."""

    p: int = Field(default=1, ge=1, description="ARCH order (lagged squared shocks).")
    q: int = Field(default=1, ge=0, description="GARCH order (lagged conditional variances).")


class EgarchParams(_GarchFamilyParams):
    """Options for the EGARCH(p, o, q) model on log-variance."""

    p: int = Field(default=1, ge=1, description="Symmetric |z| shock order.")
    o: int = Field(default=1, ge=1, description="Asymmetric (signed z) shock order.")
    q: int = Field(default=1, ge=0, description="Log-variance autoregressive order.")


class GjrGarchParams(_GarchFamilyParams):
    """Options for the GJR-GARCH(p, o, q) model with a leverage term."""

    p: int = Field(default=1, ge=1, description="ARCH order (lagged squared shocks).")
    o: int = Field(
        default=1, ge=1, description="Leverage order (squared shocks gated on negative sign)."
    )
    q: int = Field(default=1, ge=0, description="GARCH order (lagged conditional variances).")


def _param_sum(fit_params: "pd.Series[float]", prefix: str) -> float:
    """Sum every fitted parameter named ``prefix[i]`` (0.0 when the order is 0)."""
    return float(sum(v for name, v in fit_params.items() if str(name).startswith(f"{prefix}[")))


def _ljung_box(values: np.ndarray, lags: int, *, name: str, subject: str) -> Diagnostic:
    from statsmodels.stats.diagnostic import acorr_ljungbox

    frame = acorr_ljungbox(values, lags=[lags])
    stat = float(frame["lb_stat"].iloc[0])
    p_value = float(frame["lb_pvalue"].iloc[0])
    return Diagnostic(
        name=name,
        statistic=stat,
        p_value=p_value,
        passed=bool(p_value >= 0.05),
        interpretation=f"Ljung-Box on {subject} up to lag {lags}. H0: no"
        " autocorrelation; passed means the model left no detectable"
        " structure at 5%.",
    )


def _fit_garch_family(
    data: pd.DataFrame,
    p: _GarchFamilyParams,
    *,
    tool: str,
    vol: Literal["GARCH", "EGARCH"],
    o: int,
    orders: tuple[int, int],
) -> ResultSet:
    from arch import arch_model

    series = prepare_series(
        data, column=p.column, transform=p.transform, min_obs=p.min_obs, tool=tool
    )
    scaled = series * 100.0  # percentage returns; rescale=False stops arch re-scaling
    model = arch_model(
        scaled,
        vol=vol,
        p=orders[0],
        o=o,
        q=orders[1],
        dist=p.dist,
        mean=p.mean,
        rescale=False,
    )
    # The optimizer (and arch's lazy standard-error computation, which
    # numerically differentiates the likelihood) probes degenerate parameter
    # regions and numpy warns (log of ~0 variance). The fit's own finiteness
    # is checked explicitly below, so this expected exploration noise is
    # silenced, not ignored.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        try:
            fit = model.fit(disp="off", show_warning=False)
        except Exception as exc:  # arch raises plain Exceptions on degenerate input
            raise ValueError(
                f"{tool}: estimation failed ({exc}); check that column {p.column!r}"
                " holds a return series with variation and enough observations"
            ) from exc
        values = fit.params.to_numpy(dtype=float)
        std_errs = fit.std_err.to_numpy(dtype=float)
        log_likelihood = float(fit.loglikelihood)

    if (
        not np.all(np.isfinite(values))
        or not np.all(np.isfinite(std_errs))
        or not np.isfinite(log_likelihood)
    ):
        raise ValueError(
            f"{tool}: the optimizer did not reach a usable fit (convergence flag"
            f" {int(fit.convergence_flag)}); the series in column {p.column!r} may"
            " be constant, near-constant or too short for this specification"
        )

    ci = fit.conf_int()
    estimates = [
        Estimate(
            name=str(name),
            value=float(fit.params[name]),
            std_error=float(fit.std_err[name]),
            t_stat=float(fit.tvalues[name]),
            p_value=float(fit.pvalues[name]),
            ci_low=float(ci.loc[name, "lower"]),
            ci_high=float(ci.loc[name, "upper"]),
        )
        for name in fit.params.index
    ]

    if vol == "EGARCH":
        persistence = _param_sum(fit.params, "beta")
        persistence_note = (
            "EGARCH persistence is sum(beta) alone — the log-variance AR"
            " coefficient; alpha and gamma are mean-zero log-variance shocks."
        )
    elif o > 0:
        persistence = (
            _param_sum(fit.params, "alpha")
            + 0.5 * _param_sum(fit.params, "gamma")
            + _param_sum(fit.params, "beta")
        )
        persistence_note = (
            "GJR persistence is sum(alpha) + 0.5*sum(gamma) + sum(beta); the"
            " half weight is E[I(eps<0)] = 1/2 under symmetric innovations."
        )
    else:
        persistence = _param_sum(fit.params, "alpha") + _param_sum(fit.params, "beta")
        persistence_note = "GARCH persistence is sum(alpha) + sum(beta)."

    scalars: dict[str, float] = {
        "log_likelihood": log_likelihood,
        "aic": float(fit.aic),
        "bic": float(fit.bic),
        "nobs": float(fit.nobs),
        "persistence": persistence,
    }

    periods = float(PERIODS_PER_YEAR[p.frequency])
    if 0.0 < persistence < 1.0:
        scalars["half_life"] = math.log(0.5) / math.log(persistence)
        omega = float(fit.params["omega"])
        if vol == "EGARCH":
            # exp of the unconditional MEAN of log-variance: E[ln s2] = omega/(1-beta).
            # By Jensen this understates sqrt(E[s2]); documented, deterministic.
            uncond_vol_pct = math.exp(0.5 * omega / (1.0 - persistence))
        else:
            uncond_var_pct = omega / (1.0 - persistence)
            uncond_vol_pct = math.sqrt(uncond_var_pct) if uncond_var_pct > 0 else float("nan")
        if math.isfinite(uncond_vol_pct):
            scalars["unconditional_vol"] = uncond_vol_pct / 100.0  # back to decimal
            scalars["unconditional_vol_annualized"] = (
                scalars["unconditional_vol"] * math.sqrt(periods)
            )
    # persistence >= 1 (IGARCH-like fit): unconditional vol and half-life are
    # undefined; the keys are omitted rather than reported as garbage.

    # arch's result properties are typed as Series-or-ndarray; materialise both
    # onto the prepared series' index so ISO dates flow into the output.
    cond_vol = pd.Series(
        np.asarray(fit.conditional_volatility, dtype=float) / 100.0,  # decimal per-period
        index=series.index,
    )
    std_resid_values = np.asarray(fit.std_resid, dtype=float)
    std_resid = pd.Series(std_resid_values, index=series.index)

    diagnostics = [
        _ljung_box(
            std_resid_values,
            p.lb_lags,
            name="ljung_box_std_resid",
            subject="the standardized residuals",
        ),
        _ljung_box(
            std_resid_values**2,
            p.lb_lags,
            name="ljung_box_std_resid_sq",
            subject="the SQUARED standardized residuals (remaining ARCH)",
        ),
        Diagnostic(
            name="convergence",
            statistic=float(fit.convergence_flag),
            p_value=None,
            passed=bool(fit.convergence_flag == 0),
            interpretation="Optimizer convergence flag (0 = converged). A"
            " nonzero flag with finite estimates is reported here rather than"
            " raised; treat the fit with caution." + persistence_note,
        ),
    ]

    return ResultSet(
        tool=tool,
        version=_VERSION,
        params=p.model_dump(),
        estimates=estimates,
        diagnostics=diagnostics,
        scalars=scalars,
        series={
            "conditional_volatility": series_from("conditional_volatility", cond_vol),
            "standardized_residuals": series_from("standardized_residuals", std_resid),
        },
        manifest=build_manifest(data, p, tool=tool, version=_VERSION, libraries=_LIBRARIES),
    )


@get_registry().register(
    name="garch",
    version=_VERSION,
    family="volatility",
    summary="Fit a GARCH(p, q) conditional volatility model by maximum"
    " likelihood, with persistence, half-life, unconditional volatility and"
    " standardized-residual diagnostics." + _SCALE_NOTE,
    params_model=GarchParams,
    preconditions=(
        "the selected column holds one regularly observed return series in decimal units",
        "at least ~250 observations; NaNs are dropped",
    ),
    gates=(
        Gate(
            check="arch_effects",
            expect=True,
            because=(
                "with no ARCH effects in the series there is no conditional"
                " heteroskedasticity to model; the fit estimates noise and its"
                " persistence is meaningless. Report the unconditional"
                " volatility instead"
            ),
        ),
    ),
)
def garch(data: pd.DataFrame, params: BaseModel) -> ResultSet:
    p = coerce_params(params, GarchParams)
    return _fit_garch_family(data, p, tool="garch", vol="GARCH", o=0, orders=(p.p, p.q))


@get_registry().register(
    name="egarch",
    version=_VERSION,
    family="volatility",
    summary="Fit an EGARCH(p, o, q) model of log conditional variance with"
    " asymmetric (leverage) terms; persistence is the beta sum on the"
    " log-variance AR." + _SCALE_NOTE,
    params_model=EgarchParams,
    preconditions=(
        "the selected column holds one regularly observed return series in decimal units",
        "at least ~250 observations; NaNs are dropped",
    ),
    gates=(
        Gate(
            check="arch_effects",
            expect=True,
            because=(
                "with no ARCH effects in the series there is no conditional"
                " heteroskedasticity to model; the fit estimates noise and its"
                " persistence is meaningless. Report the unconditional"
                " volatility instead"
            ),
        ),
    ),
)
def egarch(data: pd.DataFrame, params: BaseModel) -> ResultSet:
    p = coerce_params(params, EgarchParams)
    return _fit_garch_family(data, p, tool="egarch", vol="EGARCH", o=p.o, orders=(p.p, p.q))


@get_registry().register(
    name="gjr_garch",
    version=_VERSION,
    family="volatility",
    summary="Fit a GJR-GARCH(p, o, q) model whose gamma terms let negative"
    " shocks raise volatility more than positive ones (leverage effect)."
    + _SCALE_NOTE,
    params_model=GjrGarchParams,
    preconditions=(
        "the selected column holds one regularly observed return series in decimal units",
        "at least ~250 observations; NaNs are dropped",
    ),
    gates=(
        Gate(
            check="arch_effects",
            expect=True,
            because=(
                "with no ARCH effects in the series there is no conditional"
                " heteroskedasticity to model; the fit estimates noise and its"
                " persistence is meaningless. Report the unconditional"
                " volatility instead"
            ),
        ),
    ),
)
def gjr_garch(data: pd.DataFrame, params: BaseModel) -> ResultSet:
    p = coerce_params(params, GjrGarchParams)
    return _fit_garch_family(data, p, tool="gjr_garch", vol="GARCH", o=p.o, orders=(p.p, p.q))
