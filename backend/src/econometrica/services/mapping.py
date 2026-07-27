"""From a profile to observations, through a confirmation nobody may skip.

`services/ingest.py` scores what each column *could* be. This module turns that
into what each column *is*, and it draws two lines that the rest of the upload
path depends on.

**Confirmation is a gate, not a formality.** §9 of the design says the user
confirms the column mapping before ingest, so `confirm_mapping` is the only
thing that produces a `ColumnMapping` with `confirmed` set, and `apply_mapping`
refuses anything else. A proposal — from the profiler or from a model — is a
suggestion until a person says otherwise.

**The user is the authority; the model is not.** So the two are constrained
differently on purpose: a user may map a column to a role the profiler never
suggested, because only the person who exported the file knows that a column of
small positive numbers is penny prices rather than returns. A model may not —
see `agents/column_mapper.py`, which can only reorder candidates this module
found admissible.
"""

import pandas as pd
from pydantic import BaseModel, Field

from econometrica.services.ingest import FileProfile, Role

#: Roles that carry an observation. A file mapped with none of these describes
#: no data, however many columns it has.
VALUE_ROLES: frozenset[Role] = frozenset({"price", "return", "factor", "volume"})

#: How close the runner-up has to score before a column counts as a real
#: choice. Wide enough that `volume` at 1.0 against `price` at 0.6 is a
#: question; narrow enough that a name-matched column is not.
AMBIGUITY_MARGIN = 0.45


class MappingError(ValueError):
    """A mapping that cannot be used, with a reason a user can act on."""


class ColumnMapping(BaseModel):
    """Which role each column plays.

    ``confirmed`` is set only by `confirm_mapping`. Constructing one directly
    gives an unconfirmed mapping, which `apply_mapping` refuses — that is the
    mechanism, and it is deliberately hard to get wrong by accident.
    """

    roles: dict[str, Role]
    confirmed: bool = False

    def columns_for(self, role: Role) -> list[str]:
        return [name for name, assigned in self.roles.items() if assigned == role]

    @property
    def date_column(self) -> str:
        columns = self.columns_for("date")
        if not columns:
            raise MappingError("no column is mapped as the date")
        return columns[0]

    @property
    def ticker_column(self) -> str | None:
        columns = self.columns_for("ticker")
        return columns[0] if columns else None

    @property
    def value_columns(self) -> list[str]:
        return [name for name, role in self.roles.items() if role in VALUE_ROLES]


class MappingProposal(BaseModel):
    """A suggestion. Never something ingest acts on."""

    roles: dict[str, Role]
    #: Why each column got its role, in the user's terms.
    rationale: dict[str, str] = Field(default_factory=dict)
    #: Columns where the runner-up scored close enough to be a real choice.
    #: These are the only thing a model is asked about.
    ambiguous: list[str] = Field(default_factory=list)

    @property
    def unambiguous(self) -> bool:
        """Whether this needs no model call.

        The common case — a date column and some obviously-priced columns — must
        cost nothing, or every upload carries a billed turn for a question with
        one answer.
        """
        return not self.ambiguous


def propose_mapping(profile: FileProfile) -> MappingProposal:
    """Each column's best candidate, and which of them were a real choice."""
    roles: dict[str, Role] = {}
    rationale: dict[str, str] = {}
    ambiguous: list[str] = []

    for column in profile.columns:
        if not column.candidates:
            roles[column.name] = "ignore"
            rationale[column.name] = "no role fits this column's contents"
            continue

        best = column.candidates[0]
        roles[column.name] = best.role
        rationale[column.name] = best.reason

        runner_up = next(
            (c for c in column.candidates[1:] if c.role != "ignore"), None
        )
        if runner_up is not None and best.score - runner_up.score <= AMBIGUITY_MARGIN:
            ambiguous.append(column.name)

    return MappingProposal(roles=roles, rationale=rationale, ambiguous=ambiguous)


def confirm_mapping(profile: FileProfile, roles: dict[str, Role]) -> ColumnMapping:
    """Validate a chosen mapping and mark it confirmed.

    The only route to a mapping `apply_mapping` will accept. Columns the caller
    did not mention default to ``ignore``: saying nothing about a column is not
    the same as asking for it.
    """
    known = {column.name for column in profile.columns}
    unknown = sorted(set(roles) - known)
    if unknown:
        raise MappingError(
            f"{profile.filename} has no column(s) named {', '.join(unknown)};"
            f" its columns are {', '.join(sorted(known))}"
        )

    complete: dict[str, Role] = {name: "ignore" for name in known}
    complete.update(roles)

    dates = [name for name, role in complete.items() if role == "date"]
    if not dates:
        raise MappingError(
            "no column is mapped as the date, so the observations have no calendar"
        )
    if len(dates) > 1:
        raise MappingError(
            f"exactly one date column is needed, but {', '.join(sorted(dates))}"
            " were all mapped as the date"
        )

    if not any(role in VALUE_ROLES for role in complete.values()):
        raise MappingError(
            "no column is mapped as a value; map at least one as"
            f" {', '.join(sorted(VALUE_ROLES))}"
        )

    tickers = [name for name, role in complete.items() if role == "ticker"]
    if len(tickers) > 1:
        raise MappingError(
            f"exactly one ticker column is needed, but {', '.join(sorted(tickers))}"
            " were all mapped as the ticker"
        )

    return ColumnMapping(roles=complete, confirmed=True)


def apply_mapping(
    frame: pd.DataFrame, mapping: ColumnMapping, profile: FileProfile
) -> pd.DataFrame:
    """The file as long-format observations: ``ts``, ``symbol``, ``field``, ``value``.

    One shape whatever the file's layout, because a hypertable stores one shape
    and a panel of any width flattens to it. A wide file names its symbol in the
    column header; a long one has a ticker column and names the *field* in the
    header instead.
    """
    if not mapping.confirmed:
        raise MappingError(
            "this mapping has not been confirmed. A proposal is a suggestion —"
            " the user confirms which column is which before anything is stored"
        )

    ts = _dates(frame[mapping.date_column])
    ticker = mapping.ticker_column
    values = [name for name in mapping.value_columns if name != ticker]
    if not values:
        raise MappingError("the mapping carries no value columns to read")

    stacked: list[pd.DataFrame] = []
    for name in values:
        numbers = _numbers(frame[name], profile)
        stacked.append(
            pd.DataFrame(
                {
                    "ts": ts,
                    # A long file says which asset each row is about; a wide one
                    # says it in the header.
                    "symbol": frame[ticker].astype(str) if ticker else name,
                    "field": mapping.roles[name],
                    "value": numbers,
                }
            )
        )

    observations = pd.concat(stacked, ignore_index=True)
    observations = observations.dropna(subset=["ts", "value"])
    return observations.reset_index(drop=True)


def _dates(column: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(column, errors="coerce", format="mixed")
    return pd.Series(pd.DatetimeIndex(parsed), index=column.index)


def _numbers(column: pd.Series, profile: FileProfile) -> pd.Series:
    """Values as floats, honouring the convention the profiler recorded.

    A decimal comma read as a thousands separator lands the numbers a thousand
    times too small, and nothing downstream would notice.
    """
    if pd.api.types.is_numeric_dtype(column):
        return column.astype(float)

    text = column.astype(str).str.strip()
    described = profile.column(str(column.name))
    if described is not None and described.decimal_comma:
        text = text.str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    else:
        text = text.str.replace(",", "", regex=False)
    return pd.to_numeric(text, errors="coerce").astype(float)
