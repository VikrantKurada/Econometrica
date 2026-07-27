"""Real data, real rate, real tool — the whole chain in one place.

Everything else in `tests/data/` checks one adapter against a fake or against
its own service. This checks the composition: Yahoo prices and a FRED treasury
yield, aligned by the Data Steward onto one calendar, handed to a registry tool
that computes an excess-return beta.

It is the test that would have caught the risk-free rate being silently dropped
— which is what happened for the whole of Phases 4 and 5 — because a CAPM run on
raw returns instead of excess returns produces a perfectly plausible beta.

Skips when either service is unreachable, so an offline run reads as "not
checked" rather than "broken".
"""

from datetime import date

import pytest

from econometrica.agents.data_steward import DataSteward
from econometrica.agents.schemas import DatasetSpec
from econometrica.data.famafrench import FACTOR_SETS, FamaFrenchFactorSource
from econometrica.data.registry import RATE_SOURCE, build_price_source
from econometrica.econ import load_tools
from econometrica.econ.registry import get_registry

load_tools()

pytestmark = pytest.mark.live


def services_are_reachable() -> bool:
    import httpx

    for url in ("https://query2.finance.yahoo.com/v1/test/getcrumb", "https://fred.stlouisfed.org/"):
        try:
            httpx.get(url, timeout=5.0)
        except httpx.HTTPError:
            return False
    return True


async def resolve(tmp_path, **overrides):
    spec = DatasetSpec(
        tickers=["AAPL", "^GSPC"],
        # Six years, because monthly is the natural frequency for a factor
        # study and `capm` wants 30 aligned observations — two years gives 23
        # once the undefined first return is dropped.
        start=date(2018, 1, 1),
        end=date(2023, 12, 31),
        frequency="M",
        return_method="simple",
        **overrides,
    )
    steward = DataSteward(
        build_price_source("yahoo", cache_root=tmp_path / "prices"),
        rate_source=build_price_source(RATE_SOURCE, cache_root=tmp_path / "rates"),
        factor_source=FamaFrenchFactorSource(),
        min_obs=12,
    )
    return await steward.resolve(spec)


async def test_a_real_risk_free_rate_reaches_a_real_capm(tmp_path):
    if not services_are_reachable():
        pytest.skip("yahoo or fred is not reachable")

    dataset = await resolve(tmp_path, risk_free="DGS3MO")

    assert dataset.report.risk_free == "DGS3MO"
    assert "risk_free" in dataset.frame.columns
    # Six years of month-end observations, aligned across both tickers.
    assert 70 <= dataset.report.rows <= 73

    monthly_rate = dataset.frame["risk_free"]
    assert monthly_rate.notna().all()
    # 2018-2023 short rates ran from roughly zero to five and a half percent a
    # year, which is 0 to 0.45% a month. If the conversion were skipped the
    # values would be near 5.0 and every alpha would be catastrophically wrong.
    assert 0.0 <= monthly_rate.max() < 0.01

    capm = get_registry().get("capm")
    result = capm.fn(
        dataset.frame,
        capm.params_model.model_validate(
            {
                "asset": "AAPL_return",
                "market": "^GSPC_return",
                "risk_free": "risk_free",
                "frequency": "M",
            }
        ),
    )

    beta = result.estimate("beta")
    assert beta is not None
    # AAPL against the S&P 500. Wide on purpose — this is checking that the
    # chain computed *a* beta from real excess returns, not what the market did.
    assert 0.5 < beta.value < 2.5
    assert result.manifest.data_fingerprint
    assert result.manifest.tool_version


@pytest.mark.parametrize("tool_name", ["ff3", "ff5", "carhart4"])
async def test_the_factor_models_can_finally_run(tmp_path, tool_name):
    """The three tools that could never run.

    They have been in the catalogue every Planner reads since Phase 2, with
    `factors` defaulting to ["mkt_rf","smb","hml"], and no source could supply a
    factor column — so `require_columns` raised and the step landed `failed`.
    This is the test that says otherwise, on real Ken French data.
    """
    if not services_are_reachable():
        pytest.skip("yahoo or fred is not reachable")

    dataset = await resolve(tmp_path, factors=tool_name)

    assert dataset.report.factors == tool_name
    # The factor file's own RF, because these factors are excess returns
    # against it — no separate rate source was configured for this run.
    assert dataset.report.risk_free == f"{tool_name} RF"

    tool = get_registry().get(tool_name)
    result = tool.fn(
        dataset.frame,
        tool.params_model.model_validate(
            {"asset": "AAPL_return", "risk_free": "risk_free", "frequency": "M"}
        ),
    )

    market = result.estimate("mkt_rf")
    assert market is not None
    assert 0.5 < market.value < 2.5, "AAPL's market loading should look like a beta"
    # Every declared factor of the set got a loading, so none was silently
    # dropped for want of a column.
    for factor in FACTOR_SETS[tool_name].factors:
        assert result.estimate(factor) is not None, factor
    assert result.manifest.data_fingerprint


async def test_the_rate_changes_the_result_it_is_part_of(tmp_path):
    """The reason the fingerprint has to include it. Two analyses that differ
    only in whether returns were taken in excess are different analyses, and a
    manifest that could not tell them apart would be claiming otherwise."""
    if not services_are_reachable():
        pytest.skip("yahoo or fred is not reachable")

    with_rate = await resolve(tmp_path, risk_free="DGS3MO")
    without = await resolve(tmp_path)

    assert without.report.risk_free is None
    assert "risk_free" not in without.frame.columns
    assert with_rate.report.fingerprint != without.report.fingerprint
