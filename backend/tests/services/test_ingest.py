"""Profiling an uploaded file.

**Deterministic on purpose**, like the Data Steward and `charts/propose.py`.
Dtypes, date parsing, cardinality, missingness and candidate roles are
arithmetic; a model that guessed at them differently on two Tuesdays would make
the reproducibility manifest a story about the weather. Task 6.7 gives a model
the one genuinely editorial job — *ranking* the candidates this module finds
admissible — and the user confirms before anything is ingested.

So the contract these tests pin is: describe the file, score every role the data
could support, and refuse rather than guess when it cannot support any.
"""

import pandas as pd
import pytest

from econometrica.services.ingest import (
    MAX_UPLOAD_BYTES,
    FileProfile,
    IngestError,
    profile_upload,
)

FRAME = pd.DataFrame(
    {
        "date": pd.date_range("2024-01-01", periods=40, freq="D"),
        "AAPL": [100.0 + i * 0.5 for i in range(40)],
        "MSFT": [200.0 + i * 0.8 for i in range(40)],
    }
)


def write_csv(tmp_path, text: str, name: str = "u.csv"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def frame_csv(tmp_path, frame: pd.DataFrame = FRAME, name: str = "u.csv"):
    path = tmp_path / name
    frame.to_csv(path, index=False)
    return path


def column(profile: FileProfile, name: str):
    found = profile.column(name)
    assert found is not None, f"no column {name!r} in {[c.name for c in profile.columns]}"
    return found


def roles(profile: FileProfile, name: str) -> list[str]:
    return [candidate.role for candidate in column(profile, name).candidates]


# --- the three formats -------------------------------------------------------


def test_a_csv_is_profiled(tmp_path):
    profile = profile_upload(frame_csv(tmp_path))

    assert profile.format == "csv"
    assert profile.rows == 40
    assert [c.name for c in profile.columns] == ["date", "AAPL", "MSFT"]


def test_an_xlsx_is_profiled(tmp_path):
    path = tmp_path / "u.xlsx"
    FRAME.to_excel(path, index=False)

    profile = profile_upload(path)

    assert profile.format == "xlsx"
    assert profile.rows == 40


def test_a_parquet_file_is_profiled(tmp_path):
    path = tmp_path / "u.parquet"
    FRAME.to_parquet(path)

    profile = profile_upload(path)

    assert profile.format == "parquet"
    assert profile.rows == 40


def test_an_unknown_extension_is_refused_by_name(tmp_path):
    path = tmp_path / "u.txt"
    path.write_text("nothing", encoding="utf-8")

    with pytest.raises(IngestError, match="txt"):
        profile_upload(path)


# --- what a column profile carries -------------------------------------------


def test_a_column_reports_its_shape(tmp_path):
    profile = profile_upload(frame_csv(tmp_path))
    aapl = column(profile, "AAPL")

    assert aapl.dtype == "number"
    assert aapl.missing == 0
    assert aapl.unique == 40
    assert aapl.minimum == pytest.approx(100.0)
    assert aapl.maximum == pytest.approx(119.5)
    assert aapl.sample


def test_missing_values_are_counted_not_dropped(tmp_path):
    frame = FRAME.copy()
    frame.loc[0:4, "AAPL"] = None

    profile = profile_upload(frame_csv(tmp_path, frame))

    assert column(profile, "AAPL").missing == 5
    assert profile.rows == 40


def test_a_text_column_reports_its_cardinality(tmp_path):
    frame = pd.DataFrame(
        {"date": ["2024-01-01"] * 4, "ticker": ["AAPL", "MSFT", "AAPL", "MSFT"]}
    )

    profile = profile_upload(frame_csv(tmp_path, frame))

    assert column(profile, "ticker").dtype == "text"
    assert column(profile, "ticker").unique == 2


# --- dates -------------------------------------------------------------------


def test_a_real_datetime_column_is_recognised(tmp_path):
    path = tmp_path / "u.parquet"
    FRAME.to_parquet(path)

    profile = profile_upload(path)

    assert column(profile, "date").dtype == "datetime"
    assert "date" in roles(profile, "date")


def test_a_date_column_that_arrived_as_text_is_recognised(tmp_path):
    """A CSV has no dtypes, so every date is text until something parses it.
    Refusing to look would make the commonest upload shape unusable."""
    profile = profile_upload(frame_csv(tmp_path))

    assert column(profile, "date").parses_as_date is True
    assert roles(profile, "date")[0] == "date"


def test_text_that_is_not_dates_does_not_become_a_date_column(tmp_path):
    frame = pd.DataFrame({"date": ["2024-01-01"] * 4, "note": ["a", "b", "c", "d"]})

    profile = profile_upload(frame_csv(tmp_path, frame))

    assert column(profile, "note").parses_as_date is False
    assert "date" not in roles(profile, "note")


def test_a_mostly_unparseable_date_column_is_not_a_date(tmp_path):
    """One stray value is a data-quality problem; a column that is mostly prose
    is not a date column at all, and saying so is the difference between a
    fixable warning and a wrong ingest."""
    frame = pd.DataFrame(
        {
            "when": ["2024-01-01", "later", "unknown", "tbd", "soon"],
            "x": [1.0, 2.0, 3.0, 4.0, 5.0],
        }
    )

    profile = profile_upload(frame_csv(tmp_path, frame))

    assert column(profile, "when").parses_as_date is False
    assert column(profile, "when").dtype == "text"


# --- numbers that arrive as text ---------------------------------------------


def test_a_european_decimal_comma_is_read_as_a_number(tmp_path):
    """`1,50` is one and a half in most of Europe. Left as text it would be
    offered no numeric role at all and the file would look unusable.

    Semicolon-separated because that is the form the convention actually
    travels in: a comma-separated file using commas for decimals is genuinely
    ambiguous, and guessing at it is not this module's job.
    """
    path = write_csv(
        tmp_path,
        "date;price;volume\n2024-01-01;1,50;100\n2024-01-02;2,75;200\n2024-01-03;3,25;300\n",
    )

    profile = profile_upload(path)

    price = column(profile, "price")
    assert price.dtype == "number"
    assert price.decimal_comma is True
    assert price.minimum == pytest.approx(1.5)
    assert price.maximum == pytest.approx(3.25)


def test_thousands_separated_integers_are_read_as_numbers(tmp_path):
    path = write_csv(tmp_path, 'date,volume\n2024-01-01,"1,200"\n2024-01-02,"3,400"\n')

    profile = profile_upload(path)

    assert column(profile, "volume").dtype == "number"
    assert column(profile, "volume").maximum == pytest.approx(3400)


# --- telling a price from a return -------------------------------------------


def test_a_positive_trending_column_scores_as_a_price(tmp_path):
    profile = profile_upload(frame_csv(tmp_path))

    assert roles(profile, "AAPL")[0] == "price"


def test_a_column_centred_near_zero_scores_as_a_return(tmp_path):
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=40, freq="D").astype(str),
            "r": [0.01, -0.02, 0.005, -0.011] * 10,
        }
    )

    profile = profile_upload(frame_csv(tmp_path, frame))

    assert roles(profile, "r")[0] == "return"
    assert "price" not in roles(profile, "r")


def test_a_negative_value_rules_out_a_price(tmp_path):
    """Half the tool registry takes logs of a price column."""
    frame = pd.DataFrame({"date": ["2024-01-01", "2024-01-02"], "x": [-5.0, 3.0]})

    profile = profile_upload(frame_csv(tmp_path, frame))

    assert "price" not in roles(profile, "x")


def test_a_known_factor_name_scores_as_a_factor(tmp_path):
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=8, freq="D").astype(str),
            "mkt_rf": [0.001, -0.002, 0.003, -0.001] * 2,
            "smb": [0.0005, -0.0004, 0.0002, -0.0003] * 2,
        }
    )

    profile = profile_upload(frame_csv(tmp_path, frame))

    assert roles(profile, "mkt_rf")[0] == "factor"
    assert roles(profile, "smb")[0] == "factor"


def test_a_volume_column_is_recognised_from_its_shape_and_name(tmp_path):
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=8, freq="D").astype(str),
            "volume": [1_000_000 + i * 1000 for i in range(8)],
        }
    )

    profile = profile_upload(frame_csv(tmp_path, frame))

    assert roles(profile, "volume")[0] == "volume"


def test_a_ticker_column_is_recognised(tmp_path):
    frame = pd.DataFrame(
        {
            "date": ["2024-01-01", "2024-01-01", "2024-01-02", "2024-01-02"],
            "symbol": ["AAPL", "MSFT", "AAPL", "MSFT"],
            "close": [100.0, 200.0, 101.0, 201.0],
        }
    )

    profile = profile_upload(frame_csv(tmp_path, frame))

    assert roles(profile, "symbol")[0] == "ticker"


# --- ambiguity is reported, not resolved -------------------------------------


def test_an_ambiguous_column_is_offered_as_several_candidates(tmp_path):
    """The point of scoring rather than deciding. A large positive integer
    column with no telling name could be a volume or a price, and inventing a
    preference is what Task 6.7 asks a model — with the user confirming."""
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=8, freq="D").astype(str),
            "v": [1200, 1300, 1250, 1400, 1350, 1500, 1450, 1600],
        }
    )

    profile = profile_upload(frame_csv(tmp_path, frame))

    assert set(roles(profile, "v")) >= {"volume", "price"}


def test_candidates_come_back_best_first_with_a_reason(tmp_path):
    profile = profile_upload(frame_csv(tmp_path))
    candidates = column(profile, "AAPL").candidates

    assert candidates == sorted(candidates, key=lambda c: -c.score)
    assert all(c.reason for c in candidates)
    assert all(0.0 < c.score <= 1.0 for c in candidates)


def test_a_column_supporting_no_role_is_offered_ignore(tmp_path):
    frame = pd.DataFrame(
        {"date": ["2024-01-01", "2024-01-02"], "notes": ["some prose", "more prose"]}
    )

    profile = profile_upload(frame_csv(tmp_path, frame))

    assert roles(profile, "notes") == ["ignore"]


# --- layout ------------------------------------------------------------------


def test_a_date_plus_several_numeric_columns_is_a_wide_layout(tmp_path):
    profile = profile_upload(frame_csv(tmp_path))

    assert profile.layout == "wide"


def test_a_date_plus_a_ticker_column_is_a_long_layout(tmp_path):
    frame = pd.DataFrame(
        {
            "date": ["2024-01-01", "2024-01-01", "2024-01-02", "2024-01-02"],
            "symbol": ["AAPL", "MSFT", "AAPL", "MSFT"],
            "close": [100.0, 200.0, 101.0, 201.0],
        }
    )

    profile = profile_upload(frame_csv(tmp_path, frame))

    assert profile.layout == "long"


# --- refusals ----------------------------------------------------------------


def test_an_empty_file_is_refused(tmp_path):
    with pytest.raises(IngestError, match="empty"):
        profile_upload(write_csv(tmp_path, ""))


def test_a_file_with_a_header_and_no_rows_is_refused(tmp_path):
    with pytest.raises(IngestError, match="no rows"):
        profile_upload(write_csv(tmp_path, "date,price\n"))


def test_a_single_column_file_is_refused(tmp_path):
    """A series with no date is not something any tool here can bind to, and
    the reason has to name what is missing."""
    with pytest.raises(IngestError, match="one column"):
        profile_upload(write_csv(tmp_path, "price\n100\n101\n102\n"))


def test_a_file_whose_header_is_not_on_the_first_row_is_refused(tmp_path):
    """The commonest broken export: a title block above the real header. pandas
    reads the title as the header and every column after the first comes back
    `Unnamed: N`, so the failure is detectable and worth naming — a user can fix
    it in seconds if told."""
    path = write_csv(
        tmp_path,
        "Monthly factor report,,\nSource: internal,,\n\ndate,AAPL,MSFT\n"
        "2024-01-02,100.0,200.0\n2024-01-03,101.0,201.0\n",
    )

    with pytest.raises(IngestError, match="header"):
        profile_upload(path)


def test_an_oversized_file_is_refused_before_it_is_read(tmp_path):
    """Checked against the file's own size on disk, so a hostile upload cannot
    exhaust memory on its way to being rejected."""
    path = frame_csv(tmp_path)

    with pytest.raises(IngestError, match="too large"):
        profile_upload(path, max_bytes=10)


def test_the_default_size_cap_is_generous_enough_to_be_real(tmp_path):
    assert MAX_UPLOAD_BYTES >= 10 * 1024 * 1024


def test_a_corrupt_parquet_file_is_refused_with_a_readable_message(tmp_path):
    path = tmp_path / "u.parquet"
    path.write_bytes(b"not parquet at all")

    with pytest.raises(IngestError, match="parquet"):
        profile_upload(path)


# --- determinism -------------------------------------------------------------


def test_profiling_the_same_file_twice_gives_the_same_answer(tmp_path):
    """No model, no sampling, no clock."""
    path = frame_csv(tmp_path)

    first = profile_upload(path)
    second = profile_upload(path)

    assert first.model_dump() == second.model_dump()


# --- the delimiter --------------------------------------------------------


def test_a_tab_separated_file_is_read(tmp_path):
    path = write_csv(tmp_path, "date\tprice\n2024-01-01\t100\n2024-01-02\t101\n", "u.csv")

    profile = profile_upload(path)

    assert [c.name for c in profile.columns] == ["date", "price"]


def test_a_delimiter_is_never_invented_from_the_alphabet(tmp_path):
    """`read_csv(sep=None)` delegates to `csv.Sniffer`, which picks *any*
    character when there is no real delimiter: on a file whose only column is
    `price` it split on the `r` and produced columns `p` and `ice`.

    A file that reads successfully but wrong is far worse than one that
    refuses, so the delimiter comes from a closed set. This asserts the mangled
    parse cannot come back.
    """
    path = write_csv(tmp_path, "price\n100\n101\n102\n")

    with pytest.raises(IngestError) as raised:
        profile_upload(path)

    assert "one column" in str(raised.value)


def test_a_quoted_comma_does_not_split_a_field(tmp_path):
    path = write_csv(
        tmp_path, 'date,name,price\n2024-01-01,"Apple, Inc.",100\n2024-01-02,"Apple, Inc.",101\n'
    )

    profile = profile_upload(path)

    assert [c.name for c in profile.columns] == ["date", "name", "price"]
    assert column(profile, "name").unique == 1


# --- against this project's own output ---------------------------------------


async def test_a_dataset_this_project_exported_profiles_correctly(tmp_path):
    """Round-tripping our own shape, which is the file a user is likeliest to
    upload — they exported it from here. Uses the synthetic source so it needs
    no network, and the column *names* are what matter: `AAPL`, `AAPL_return`
    and `risk_free` all have to land on the right roles.
    """
    from econometrica.agents.data_steward import DataSteward
    from econometrica.agents.schemas import DatasetSpec
    from econometrica.data.synthetic import SyntheticPriceSource

    dataset = await DataSteward(SyntheticPriceSource()).resolve(
        DatasetSpec(tickers=["AAPL", "MSFT"], start="2020-01-01", end="2020-06-30")
    )
    frame = dataset.frame.reset_index(names="date")
    path = tmp_path / "exported.csv"
    frame.to_csv(path, index=False)

    profile = profile_upload(path)

    assert profile.layout == "wide"
    assert roles(profile, "date")[0] == "date"
    assert roles(profile, "AAPL")[0] == "price"
    assert roles(profile, "MSFT")[0] == "price"
    assert roles(profile, "AAPL_return")[0] == "return"
    assert roles(profile, "MSFT_return")[0] == "return"
