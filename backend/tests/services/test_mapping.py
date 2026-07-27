"""Turning a profile into a mapping, and refusing to act on an unconfirmed one.

Three layers, and the boundaries between them are the whole design:

* `propose_mapping` is deterministic — it takes each column's best candidate and
  says which columns were a real choice rather than a foregone one.
* a model may reorder those candidates (`agents/column_mapper.py`) and nothing
  else.
* **`confirm_mapping` is the only thing that produces a mapping ingest will
  act on**, because §9 of the design requires the user to confirm before
  anything is stored.

The user is the authority and the model is not, so the constraints differ
deliberately: a user may override a column to a role the profiler did not
suggest, and a model may not.
"""

import pandas as pd
import pytest

from econometrica.services.ingest import profile_upload
from econometrica.services.mapping import (
    ColumnMapping,
    MappingError,
    apply_mapping,
    confirm_mapping,
    propose_mapping,
)

WIDE = pd.DataFrame(
    {
        "date": pd.date_range("2024-01-01", periods=6, freq="D").astype(str),
        "AAPL": [100.0, 101.0, 102.5, 101.5, 103.0, 104.0],
        "MSFT": [200.0, 201.0, 202.5, 201.5, 203.0, 204.0],
    }
)

LONG = pd.DataFrame(
    {
        "date": ["2024-01-01", "2024-01-01", "2024-01-02", "2024-01-02"],
        "symbol": ["AAPL", "MSFT", "AAPL", "MSFT"],
        "close": [100.0, 200.0, 101.0, 201.0],
    }
)


def profile_of(tmp_path, frame: pd.DataFrame = WIDE):
    path = tmp_path / "u.csv"
    frame.to_csv(path, index=False)
    return profile_upload(path)


# --- the deterministic proposal ----------------------------------------------


def test_a_clear_file_proposes_the_obvious_mapping(tmp_path):
    proposal = propose_mapping(profile_of(tmp_path))

    assert proposal.roles["date"] == "date"
    assert proposal.roles["AAPL"] == "price"
    assert proposal.roles["MSFT"] == "price"


def test_a_clear_file_needs_no_model(tmp_path):
    """The common case must be free. A file whose every column has one obvious
    role is not a question worth asking a model, and asking anyway would put a
    billed call behind every upload."""
    proposal = propose_mapping(profile_of(tmp_path))

    assert proposal.unambiguous is True
    assert proposal.ambiguous == []


def test_a_column_with_two_close_candidates_is_marked_ambiguous(tmp_path):
    """A large positive integer column could be a volume or a price. Saying so
    is what gives the model — and the user — something real to decide."""
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=6, freq="D").astype(str),
            "v": [1200, 1300, 1250, 1400, 1350, 1500],
        }
    )

    proposal = propose_mapping(profile_of(tmp_path, frame))

    assert "v" in proposal.ambiguous
    assert proposal.unambiguous is False


def test_every_proposed_column_carries_a_reason(tmp_path):
    proposal = propose_mapping(profile_of(tmp_path))

    assert set(proposal.rationale) == set(proposal.roles)
    assert all(proposal.rationale.values())


def test_a_long_file_proposes_a_ticker_column(tmp_path):
    proposal = propose_mapping(profile_of(tmp_path, LONG))

    assert proposal.roles["symbol"] == "ticker"
    assert proposal.roles["close"] == "price"


# --- confirmation is the gate ------------------------------------------------


def test_a_proposal_is_not_something_ingest_will_act_on(tmp_path):
    """The safety property this task exists for. A model's suggestion is not a
    decision, and nothing downstream may treat it as one."""
    profile = profile_of(tmp_path)
    proposal = propose_mapping(profile)

    assert ColumnMapping(roles=proposal.roles).confirmed is False

    with pytest.raises(MappingError, match="confirm"):
        apply_mapping(WIDE, ColumnMapping(roles=proposal.roles), profile)


def test_confirming_produces_a_mapping_ingest_accepts(tmp_path):
    profile = profile_of(tmp_path)
    proposal = propose_mapping(profile)

    mapping = confirm_mapping(profile, proposal.roles)

    assert mapping.confirmed is True
    assert apply_mapping(WIDE, mapping, profile) is not None


def test_confirming_a_column_the_file_does_not_have_is_refused(tmp_path):
    profile = profile_of(tmp_path)

    with pytest.raises(MappingError, match="GOOG"):
        confirm_mapping(profile, {"date": "date", "GOOG": "price"})


def test_a_mapping_with_no_date_column_is_refused(tmp_path):
    profile = profile_of(tmp_path)

    with pytest.raises(MappingError, match="date"):
        confirm_mapping(profile, {"date": "ignore", "AAPL": "price", "MSFT": "price"})


def test_a_mapping_with_two_date_columns_is_refused(tmp_path):
    profile = profile_of(tmp_path)

    with pytest.raises(MappingError, match="one date"):
        confirm_mapping(profile, {"date": "date", "AAPL": "date", "MSFT": "price"})


def test_a_mapping_with_no_values_is_refused(tmp_path):
    """A date column and nothing else describes no observations at all."""
    profile = profile_of(tmp_path)

    with pytest.raises(MappingError, match="value"):
        confirm_mapping(profile, {"date": "date", "AAPL": "ignore", "MSFT": "ignore"})


def test_a_column_left_out_entirely_defaults_to_ignore(tmp_path):
    """A user who says nothing about a column has not chosen to ingest it."""
    profile = profile_of(tmp_path)

    mapping = confirm_mapping(profile, {"date": "date", "AAPL": "price"})

    assert mapping.roles["MSFT"] == "ignore"


def test_the_user_may_override_a_role_the_profiler_did_not_suggest(tmp_path):
    """The user is the authority; the profiler only scores what it can see. A
    column of small positive numbers the profiler read as returns really might
    be penny prices, and only the person who exported it knows."""
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=6, freq="D").astype(str),
            "p": [0.01, 0.02, 0.015, 0.011, 0.02, 0.017],
        }
    )
    profile = profile_of(tmp_path, frame)
    assert "price" not in [c.role for c in profile.column("p").candidates]

    mapping = confirm_mapping(profile, {"date": "date", "p": "price"})

    assert mapping.roles["p"] == "price"


# --- applying it -------------------------------------------------------------


def test_a_wide_file_becomes_long_observations(tmp_path):
    """One row per (date, symbol, field), which is the shape a hypertable
    stores and the shape a panel of any width flattens to."""
    profile = profile_of(tmp_path)
    mapping = confirm_mapping(profile, {"date": "date", "AAPL": "price", "MSFT": "price"})

    observations = apply_mapping(WIDE, mapping, profile)

    assert list(observations.columns) == ["ts", "symbol", "field", "value"]
    assert len(observations) == 12
    assert set(observations["symbol"]) == {"AAPL", "MSFT"}
    assert set(observations["field"]) == {"price"}
    assert observations["ts"].dtype.kind == "M"


def test_a_long_file_keeps_its_own_ticker_column(tmp_path):
    profile = profile_of(tmp_path, LONG)
    mapping = confirm_mapping(
        profile, {"date": "date", "symbol": "ticker", "close": "price"}
    )

    observations = apply_mapping(LONG, mapping, profile)

    assert len(observations) == 4
    assert set(observations["symbol"]) == {"AAPL", "MSFT"}


def test_ignored_columns_do_not_reach_the_observations(tmp_path):
    profile = profile_of(tmp_path)
    mapping = confirm_mapping(profile, {"date": "date", "AAPL": "price"})

    observations = apply_mapping(WIDE, mapping, profile)

    assert set(observations["symbol"]) == {"AAPL"}


def test_a_decimal_comma_column_is_parsed_on_the_way_in(tmp_path):
    """The profiler recorded the convention; ingest has to honour it or the
    numbers arrive a thousand times too small."""
    path = tmp_path / "eu.csv"
    path.write_text(
        "date;price\n2024-01-01;1,50\n2024-01-02;2,75\n2024-01-03;3,25\n",
        encoding="utf-8",
    )
    profile = profile_upload(path)
    frame = pd.read_csv(path, sep=";", dtype=str)
    mapping = confirm_mapping(profile, {"date": "date", "price": "price"})

    observations = apply_mapping(frame, mapping, profile)

    assert observations["value"].tolist() == pytest.approx([1.5, 2.75, 3.25])


def test_rows_with_no_value_are_dropped_not_carried(tmp_path):
    frame = WIDE.copy()
    frame.loc[0, "AAPL"] = None
    profile = profile_of(tmp_path, frame)
    mapping = confirm_mapping(profile, {"date": "date", "AAPL": "price", "MSFT": "price"})

    observations = apply_mapping(frame, mapping, profile)

    assert len(observations) == 11
    assert observations["value"].notna().all()


def test_the_same_file_maps_identically_twice(tmp_path):
    profile = profile_of(tmp_path)
    mapping = confirm_mapping(profile, {"date": "date", "AAPL": "price", "MSFT": "price"})

    first = apply_mapping(WIDE, mapping, profile)
    second = apply_mapping(WIDE, mapping, profile)

    assert first.equals(second)
