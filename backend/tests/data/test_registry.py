"""The one place that knows every price source.

Mirrors `llm/registry.py`: the API layer names a source rather than importing
an adapter, so adding one means adding an entry. Two of the tests here are
really about the honesty seam rather than about the registry — the Data Steward
raises its `synthetic_data` risk flag on a substring match, and the settings
enum has to agree with what can actually be built.
"""

from datetime import date
from typing import get_args

import pytest

from econometrica.agents.data_steward import DataUnavailableError, PriceSource
from econometrica.config import Settings
from econometrica.data.cache import CachingPriceSource
from econometrica.data.registry import SPECS, build_price_source, names, spec
from econometrica.data.synthetic import SyntheticPriceSource
from econometrica.data.yahoo import YahooPriceSource


def build(name: str, tmp_path):
    return build_price_source(name, cache_root=tmp_path)


def test_every_registered_name_builds_something_that_is_a_price_source(tmp_path):
    for name in names():
        source = build(name, tmp_path)
        assert isinstance(source, PriceSource)
        assert source.label


def test_an_unknown_name_raises_and_names_what_is_known(tmp_path):
    with pytest.raises(KeyError, match="synthetic"):
        build("stooq", tmp_path)


def test_the_settings_enum_and_the_registry_agree():
    """A value that passes settings validation must always build. If they drift,
    the failure surfaces as a 500 on the first run rather than at startup."""
    allowed = set(get_args(Settings.model_fields["price_source"].annotation))

    assert allowed == set(names())


# --- caching ----------------------------------------------------------------


def test_a_network_source_is_wrapped_in_the_cache(tmp_path):
    source = build("yahoo", tmp_path)

    assert isinstance(source, CachingPriceSource)


def test_the_synthetic_source_is_not_cached(tmp_path):
    """It is deterministic and instant. Caching it would add disk churn to buy
    nothing, and its reproducibility already comes from the seed."""
    assert isinstance(build("synthetic", tmp_path), SyntheticPriceSource)


def test_caching_does_not_change_what_the_report_says_about_the_source(tmp_path):
    """`label` is what reaches `DataQualityReport.source`."""
    assert build("yahoo", tmp_path).label == YahooPriceSource().label


# --- the honesty seam --------------------------------------------------------


def test_only_the_synthetic_source_claims_to_be_synthetic(tmp_path):
    """`DataSteward.resolve` raises its `synthetic_data` risk flag when
    `"synthetic" in label.lower()`. A real adapter whose label contained the
    word would tell every reader its market data was generated; the synthetic
    one omitting it would hide that they were."""
    for name in names():
        label = build(name, tmp_path).label.lower()
        assert ("synthetic" in label) == (name == "synthetic"), name


def test_every_spec_has_a_factory():
    from econometrica.data.registry import DEFAULT_FACTORIES

    assert {entry.name for entry in SPECS} == set(DEFAULT_FACTORIES)


def test_a_spec_is_retrievable_by_name():
    assert spec("yahoo").cached is True
    assert spec("none").cached is False


# --- the unconfigured default -----------------------------------------------


async def test_the_default_refuses_with_an_explanation_rather_than_empty_data(tmp_path):
    """Left unset, a run must not invent data. The message has to name the
    ticker and say what to do, because it is the first thing a new user sees."""
    source = build("none", tmp_path)

    with pytest.raises(DataUnavailableError, match="AAPL"):
        await source.prices("AAPL", start=date(2024, 1, 1), end=date(2024, 2, 1))


# --- what the application selects -------------------------------------------


def test_get_price_source_honours_the_setting(monkeypatch, tmp_path):
    from econometrica.api.deps import get_price_source

    monkeypatch.setenv("ECONOMETRICA_PRICE_SOURCE", "yahoo")
    monkeypatch.setenv("ECONOMETRICA_STORAGE_DIR", str(tmp_path))

    assert isinstance(get_price_source(), CachingPriceSource)


def test_get_price_source_still_defaults_to_refusing(monkeypatch, tmp_path):
    from econometrica.api.deps import get_price_source

    monkeypatch.setenv("ECONOMETRICA_PRICE_SOURCE", "none")
    monkeypatch.setenv("ECONOMETRICA_STORAGE_DIR", str(tmp_path))

    assert "none" in get_price_source().label.lower()


def test_the_cache_lives_under_the_storage_directory(monkeypatch, tmp_path):
    """It has to be somewhere a user can delete without consequence, and
    `storage/` is gitignored and already holds the keystore."""
    from econometrica.api.deps import get_price_source

    monkeypatch.setenv("ECONOMETRICA_PRICE_SOURCE", "yahoo")
    monkeypatch.setenv("ECONOMETRICA_STORAGE_DIR", str(tmp_path))

    source = get_price_source()
    assert isinstance(source, CachingPriceSource)
    assert tmp_path in source._root.parents or source._root == tmp_path
