"""Profiling an uploaded file, before anything is ingested.

**Deterministic, like the Data Steward and `charts/propose.py`.** Dtypes, date
parsing, cardinality, missingness and the roles a column *could* play are
arithmetic. A model that scored them differently on two Tuesdays would make the
reproducibility manifest a story about the weather.

So this module decides what is **admissible** and never what is chosen. Every
column comes back with each role its data can support, scored, with the reason;
Task 6.7 asks a model to rank those candidates, and §9 of the design requires
the user to confirm the mapping before ingest. A model may not invent a column,
invent a role, or pick one this module scored as inadmissible.

The refusals matter as much as the profiles. A file whose header is on row 3 is
the commonest broken export there is, and "column `Unnamed: 2` is not usable" is
a puzzle where "the header is not on the first row" is a ten-second fix.
"""

import math
import re
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, Field

#: Large enough for a few decades of daily data across a wide panel, small
#: enough that a single upload cannot exhaust memory. Checked against the file's
#: size on disk *before* it is opened.
MAX_UPLOAD_BYTES = 64 * 1024 * 1024

Role = Literal["date", "ticker", "price", "return", "volume", "factor", "ignore"]
Dtype = Literal["number", "datetime", "text", "boolean"]

#: How many rows a verdict is drawn from. Profiling is a description, not an
#: analysis, and reading a whole panel to decide a column is text buys nothing.
_SCAN_ROWS = 1000

#: Fraction of non-null values that must parse before a text column is called a
#: date. One stray value is a data-quality problem; a column that is mostly
#: prose is not a date column at all.
_DATE_THRESHOLD = 0.8

#: Column names that settle a role on their own. Matched on the normalised
#: name, so `Adj Close` and `adj_close` are the same hint.
_NAME_HINTS: dict[Role, frozenset[str]] = {
    "date": frozenset({"date", "datetime", "time", "timestamp", "period", "month", "day", "dt"}),
    "ticker": frozenset({"ticker", "symbol", "asset", "security", "permno", "cusip", "isin", "id"}),
    "price": frozenset(
        {"price", "close", "adjclose", "adjustedclose", "open", "high", "low", "nav"}
    ),
    "return": frozenset({"return", "returns", "ret", "pctchange", "logreturn", "excessreturn"}),
    "volume": frozenset({"volume", "vol", "shares", "turnover", "quantity", "qty"}),
    "factor": frozenset({"mktrf", "smb", "hml", "rmw", "cma", "mom", "umd", "rf", "riskfree"}),
}

#: `1,50` — a decimal comma, with optional thousands dots.
_DECIMAL_COMMA = re.compile(r"^-?\d{1,3}(?:\.\d{3})*,\d+$|^-?\d+,\d+$")
#: `1,200` — a thousands comma.
_THOUSANDS_COMMA = re.compile(r"^-?\d{1,3}(?:,\d{3})+$")

_UNNAMED = re.compile(r"^Unnamed: \d+$")

#: Every delimiter a real export uses, and nothing else — see `_sniff_delimiter`
#: for why the set is closed.
_DELIMITERS = (",", ";", "\t", "|")

_SUFFIXES = {".csv": "csv", ".xlsx": "xlsx", ".xls": "xlsx", ".parquet": "parquet"}


class IngestError(ValueError):
    """The file cannot be profiled, with a reason a user can act on."""


class RoleCandidate(BaseModel):
    """One role a column's data could support."""

    role: Role
    #: 0 to 1. Comparable within a column, not across files.
    score: float = Field(gt=0.0, le=1.0)
    #: Why this scored, in the user's terms — shown beside the picker.
    reason: str


class ColumnProfile(BaseModel):
    name: str
    dtype: Dtype
    #: Values present, and values absent. Missing rows are counted, never
    #: dropped: how much is missing is part of whether the file is usable.
    present: int
    missing: int
    unique: int
    minimum: float | None = None
    maximum: float | None = None
    #: A few values as they appear in the file, for the confirmation screen.
    sample: list[str] = Field(default_factory=list)
    #: Whether text values parsed as dates. False for genuinely typed columns.
    parses_as_date: bool = False
    #: Whether numbers are written with a decimal comma, which ingest must know.
    decimal_comma: bool = False
    #: Every role the data supports, best first. Never a single decision.
    candidates: list[RoleCandidate] = Field(default_factory=list)


class FileProfile(BaseModel):
    filename: str
    format: Literal["csv", "xlsx", "parquet"]
    rows: int
    columns: list[ColumnProfile]
    #: `wide` is a date plus one column per asset; `long` is a date plus a
    #: ticker column plus values. The mapping screen differs between them.
    layout: Literal["wide", "long", "unknown"] = "unknown"

    def column(self, name: str) -> ColumnProfile | None:
        return next((c for c in self.columns if c.name == name), None)


def profile_upload(
    path: Path, *, max_bytes: int = MAX_UPLOAD_BYTES
) -> FileProfile:
    """Describe an uploaded file and score the roles its columns could play."""
    fmt = _format_of(path)

    # Before opening it. A hostile upload must not be able to exhaust memory on
    # its way to being rejected.
    size = path.stat().st_size
    if size > max_bytes:
        raise IngestError(
            f"{path.name} is too large: {size} bytes against a limit of {max_bytes}"
        )
    if size == 0:
        raise IngestError(f"{path.name} is empty")

    frame, delimiter = _read(path, fmt)
    _reject_unusable(frame, path.name)

    columns = [_profile_column(frame[name], delimiter) for name in frame.columns]
    return FileProfile(
        filename=path.name,
        format=fmt,
        rows=len(frame),
        columns=columns,
        layout=_layout(columns),
    )


# --- reading ----------------------------------------------------------------


def _format_of(path: Path) -> Literal["csv", "xlsx", "parquet"]:
    fmt = _SUFFIXES.get(path.suffix.lower())
    if fmt is None:
        raise IngestError(
            f"cannot read {path.suffix or path.name!r} files; upload a"
            f" {', '.join(sorted(set(_SUFFIXES)))} file"
        )
    return fmt  # type: ignore[return-value]


def _read(path: Path, fmt: str) -> tuple[pd.DataFrame, str | None]:
    """The frame, and the delimiter it was read with (None where irrelevant)."""
    try:
        if fmt == "csv":
            delimiter = _sniff_delimiter(path)
            frame = pd.read_csv(
                path,
                sep=delimiter,
                engine="python",
                nrows=_SCAN_ROWS,
                # Excel writes a BOM on almost every CSV it exports; without
                # this the first column is named "﻿date" and matches
                # nothing.
                encoding="utf-8-sig",
            )
            return frame, delimiter
        if fmt == "xlsx":
            return pd.read_excel(path, nrows=_SCAN_ROWS), None
        return pd.read_parquet(path), None
    except IngestError:
        raise
    except Exception as exc:
        raise IngestError(f"{path.name} could not be read as {fmt}: {exc}") from exc


def _sniff_delimiter(path: Path) -> str:
    """Pick the delimiter from a fixed set, never from the whole alphabet.

    `pandas.read_csv(sep=None)` delegates to `csv.Sniffer`, which will choose an
    arbitrary character when there is no real delimiter — on a single-column
    file holding `price`, it split on the `r` and produced columns `p` and
    `ice`. A mangled file that reads successfully is far worse than one that
    refuses, so the candidates are enumerated and comma is the fallback.
    """
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        header = next((line for line in handle if line.strip()), "")

    counts = {candidate: header.count(candidate) for candidate in _DELIMITERS}
    best = max(counts, key=lambda candidate: (counts[candidate], -_DELIMITERS.index(candidate)))
    return best if counts[best] else ","


def _reject_unusable(frame: pd.DataFrame, filename: str) -> None:
    if frame.empty and not len(frame.columns):
        raise IngestError(f"{filename} is empty")

    names = [str(name) for name in frame.columns]
    unnamed = sum(1 for name in names if _UNNAMED.match(name))
    if names and unnamed >= max(1, len(names) // 2):
        raise IngestError(
            f"{filename}: the header does not appear to be on the first row"
            f" — {unnamed} of {len(names)} columns came back unnamed. Remove any"
            " title or note rows above the column names and upload it again"
        )

    if len(names) < 2:
        raise IngestError(
            f"{filename} has only one column. An upload needs at least a date"
            " column and one column of values"
        )

    if len(frame) == 0:
        raise IngestError(f"{filename} has column names but no rows")


# --- per-column profiling ----------------------------------------------------


def _profile_column(series: pd.Series, delimiter: str | None = None) -> ColumnProfile:
    name = str(series.name)
    present = int(series.notna().sum())
    values = series.dropna()

    numeric, decimal_comma = _as_numeric(values, delimiter)
    is_date = _parses_as_date(values) if numeric is None else False

    if numeric is not None:
        dtype: Dtype = "number"
    elif is_date or pd.api.types.is_datetime64_any_dtype(series):
        dtype = "datetime"
    elif pd.api.types.is_bool_dtype(series):
        dtype = "boolean"
    else:
        dtype = "text"

    profile = ColumnProfile(
        name=name,
        dtype=dtype,
        present=present,
        missing=int(series.isna().sum()),
        unique=int(values.nunique()),
        minimum=_finite(numeric.min()) if numeric is not None and len(numeric) else None,
        maximum=_finite(numeric.max()) if numeric is not None and len(numeric) else None,
        sample=[str(v) for v in values.head(3)],
        parses_as_date=is_date or bool(pd.api.types.is_datetime64_any_dtype(series)),
        decimal_comma=decimal_comma,
    )
    profile.candidates = _score(profile, numeric, len(series))
    return profile


def _as_numeric(values: pd.Series, delimiter: str | None) -> tuple[pd.Series | None, bool]:
    """The column as numbers, and whether a decimal comma was used.

    Text is attempted because a CSV has no dtypes: a price column and a note
    column arrive identically, and only parsing tells them apart.

    **The delimiter disambiguates the comma**, which nothing else can. `1,200`
    is `1.2` under a decimal comma and `1200` under a thousands comma, and the
    string alone cannot say which. But a file that uses commas for decimals
    cannot also use them to separate fields — that is exactly why such exports
    are semicolon-separated — so a comma-delimited file means thousands, and
    any other delimiter admits decimals.
    """
    if pd.api.types.is_bool_dtype(values) or pd.api.types.is_datetime64_any_dtype(values):
        return None, False
    if pd.api.types.is_numeric_dtype(values):
        return values.astype(float), False
    if values.empty:
        return None, False

    text = values.astype(str).str.strip()
    decimals_possible = delimiter is not None and delimiter != ","

    if decimals_possible and text.str.fullmatch(_DECIMAL_COMMA.pattern).all():
        cleaned = text.str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
        return pd.to_numeric(cleaned).astype(float), True
    if text.str.fullmatch(_THOUSANDS_COMMA.pattern).all():
        return pd.to_numeric(text.str.replace(",", "", regex=False)).astype(float), False

    converted = pd.to_numeric(text, errors="coerce")
    if converted.notna().all():
        return converted.astype(float), False
    return None, False


def _parses_as_date(values: pd.Series) -> bool:
    if values.empty:
        return False
    if pd.api.types.is_datetime64_any_dtype(values):
        return True
    if pd.api.types.is_numeric_dtype(values):
        return False
    parsed = pd.to_datetime(values.astype(str), errors="coerce", format="mixed")
    return bool(parsed.notna().mean() >= _DATE_THRESHOLD)


def _finite(value: Any) -> float | None:
    number = float(value)
    return number if math.isfinite(number) else None


# --- scoring -----------------------------------------------------------------


def _normalise(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _hinted(name: str, role: Role) -> bool:
    normalised = _normalise(name)
    hints = _NAME_HINTS[role]
    return normalised in hints or any(
        hint in normalised for hint in hints if len(hint) > 3
    )


def _score(
    profile: ColumnProfile, numeric: pd.Series | None, rows: int
) -> list[RoleCandidate]:
    """Every role this column's data supports, with a reason for each.

    Scores are comparable within a column only. They exist to order the picker
    and to let Task 6.7's model rank, never to cross a threshold and decide.
    """
    found: list[RoleCandidate] = []

    def offer(role: Role, score: float, reason: str) -> None:
        found.append(RoleCandidate(role=role, score=min(score, 1.0), reason=reason))

    if profile.parses_as_date:
        offer(
            "date",
            1.0 if _hinted(profile.name, "date") else 0.9,
            "values parse as dates",
        )

    if numeric is not None and len(numeric):
        found.extend(_numeric_roles(profile, numeric))
    elif profile.dtype == "text" and not profile.parses_as_date:
        found.extend(_text_roles(profile, rows))

    if not found:
        offer("ignore", 1.0, "no role fits this column's contents")

    return sorted(found, key=lambda candidate: (-candidate.score, candidate.role))


def _numeric_roles(profile: ColumnProfile, numeric: pd.Series) -> list[RoleCandidate]:
    found: list[RoleCandidate] = []
    low = float(numeric.min())
    high = float(numeric.max())
    magnitude = float(numeric.abs().median())
    centred = abs(float(numeric.mean())) < 0.1 and float(numeric.abs().max()) < 1.0
    whole = bool((numeric == numeric.round()).all())

    if _hinted(profile.name, "factor"):
        found.append(
            RoleCandidate(
                role="factor", score=1.0, reason="the name matches a known factor"
            )
        )
    if centred:
        found.append(
            RoleCandidate(
                role="return",
                score=1.0 if _hinted(profile.name, "return") else 0.85,
                reason="values are centred near zero and bounded by one",
            )
        )
        if not _hinted(profile.name, "factor"):
            found.append(
                RoleCandidate(
                    role="factor", score=0.4, reason="a factor is shaped like a return"
                )
            )
    if low >= 0 and whole and magnitude >= 1000:
        found.append(
            RoleCandidate(
                role="volume",
                score=1.0 if _hinted(profile.name, "volume") else 0.7,
                reason="whole non-negative values of a size typical of volumes",
            )
        )
    # A negative price is not a price: half the tool registry takes logs of one.
    if low > 0 and not centred:
        found.append(
            RoleCandidate(
                role="price",
                score=1.0 if _hinted(profile.name, "price") else 0.6,
                reason=f"strictly positive values from {low:g} to {high:g}",
            )
        )
    return found


def _text_roles(profile: ColumnProfile, rows: int) -> list[RoleCandidate]:
    # A ticker repeats: a long panel names each asset once per date. A column of
    # unique strings is prose, an identifier, or a mistake.
    repeats = profile.unique < max(2, profile.present)
    if _hinted(profile.name, "ticker") or (repeats and profile.unique <= max(2, rows // 2)):
        return [
            RoleCandidate(
                role="ticker",
                score=1.0 if _hinted(profile.name, "ticker") else 0.7,
                reason=f"{profile.unique} distinct values repeating across rows",
            )
        ]
    return []


def _layout(columns: list[ColumnProfile]) -> Literal["wide", "long", "unknown"]:
    """Whether values are spread across columns or stacked under a ticker."""
    def best(profile: ColumnProfile) -> str:
        return profile.candidates[0].role if profile.candidates else "ignore"

    roles = [best(profile) for profile in columns]
    if "date" not in roles:
        return "unknown"
    if "ticker" in roles:
        return "long"
    if sum(1 for role in roles if role in ("price", "return", "factor", "volume")) >= 1:
        return "wide"
    return "unknown"
