# Uploaded data in a run: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a run read an ingested upload, so a project's own series can be
analysed alongside fetched tickers in one frame.

**Architecture:** A run-scoped `ProjectPriceSource` wraps whichever market
source is configured and consults the project's uploads first. It records which
source served each ticker; `DataSteward` reads that back duck-typed and
discloses it as a `mixed_sources` flag. `api/deps.py` and `data/registry.py` are
untouched — the registry stays the set of configurable *global* sources.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async, pandas 3.0.5, pytest.

**Design note:** `docs/plans/2026-07-30-uploaded-price-source-design.md`.

## Global Constraints

- Everything runs under `uv run` from `backend/`. There is no venv to activate.
- **TDD, strictly.** Write the test, run it, watch it fail with the expected
  error, then implement. Several real bugs here were found only that way.
- `uv run ruff check src tests alembic` and `uv run mypy src` must stay clean.
- Comments explain *why* a choice was forced, never what the next line does.
- Conventional Commits, reasoning in the body. Write the message to a file and
  use `git commit -F` — PowerShell mangles `-m` when the message has quotes.
- ~40 tests need the database: `docker compose up -d db --wait` first.
- `data/` must not import from `agents/`. `tests/data/test_layering.py` enforces
  it with a subprocess check of both import orders.
- No migration in this plan. Nothing here changes a table.

---

## Two corrections to the design note

**1. The label guard belongs in `services/datasets.py`, not `services/ingest.py`.**
`source_label()` is defined at `services/datasets.py:25` and called from
`ingest_observations` at line 68. `services/ingest.py` profiles an upload and
never builds a label. Task 2 targets the real location.

**2. `shadowed_symbol` cannot be determined without a counterfactual fetch, so
it is not implemented.** To know that Yahoo *also* carries a symbol the upload
served, something has to ask Yahoo — a network call whose only purpose is to
populate a warning, and one that would make an upload-only run require the
network it was supposed to avoid.

`mixed_sources` already carries strictly more information at no cost: it names
the source of **every** ticker, so a symbol served from an upload is listed as
such by name. The concern behind the flag was silent substitution; a report
saying `AAPL from upload: my-prices.csv (ingested 2026-07-30)` is not silent.

**This needs a nod before Task 3 lands.** If you want the probe anyway, it is
an extra step in Task 1 and an extra flag in Task 3.

---

## File structure

| File | Responsibility |
|---|---|
| `src/econometrica/data/project_source.py` | **new.** The composite and its async factory. Owns the symbol map and the record of what it served. |
| `tests/data/test_project_source.py` | **new.** The composite against a fake market source and a real session. |
| `src/econometrica/services/datasets.py` | `source_label` refuses a filename that would collide with the `synthetic_data` seam. |
| `tests/services/test_datasets.py` | The guard. Add to the existing file if present; create if not. |
| `src/econometrica/agents/data_steward.py` | Reads `provenance` back off the source and raises `mixed_sources`. |
| `tests/agents/test_data_steward.py` | The flag's content and its absence. |
| `src/econometrica/api/routers/runs.py` | `_build` becomes async and composes; `rerun` gains project scope and a 409. |
| `tests/api/test_runs.py` | Route-level wiring and the re-run regression. |

---

### Task 1: `ProjectPriceSource` and its factory

**Files:**
- Create: `backend/src/econometrica/data/project_source.py`
- Test: `backend/tests/data/test_project_source.py`

**Interfaces:**
- Consumes: `UploadedPriceSource(session, dataset_id, *, label)` from
  `data/uploaded.py`; `PriceSource` and `DataUnavailableError` from
  `data/base.py`; `Dataset` and `Observation` from `db.models`.
- Produces:
  - `ProjectPriceSource` with `label: str`, `provenance: dict[str, str]`,
    `prices(ticker, *, start: date, end: date) -> pd.Series`
  - `async build_project_source(session: AsyncSession, project_id: UUID, *, market: PriceSource) -> PriceSource`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/data/test_project_source.py`:

```python
"""A project's uploads layered over a market source.

The fake market source is deliberately not a mock: these tests are about which
source answers, so the thing that stands in for Yahoo has to be able to answer
and to refuse, and to say which it did.
"""

from datetime import UTC, date, datetime

import pandas as pd
import pytest

from econometrica.data.base import DataUnavailableError
from econometrica.data.project_source import ProjectPriceSource, build_project_source
from econometrica.db.models import Dataset, Observation, Project

START, END = date(2024, 1, 1), date(2024, 1, 31)


class FakeMarket:
    """Answers for the symbols it was given, refuses everything else."""

    label = "Fake Market (dividend-adjusted)"

    def __init__(self, symbols: set[str]) -> None:
        self._symbols = symbols
        self.asked: list[str] = []

    async def prices(self, ticker: str, *, start: date, end: date) -> pd.Series:
        self.asked.append(ticker)
        if ticker not in self._symbols:
            raise DataUnavailableError(f"{ticker}: not listed")
        index = pd.to_datetime(["2024-01-02", "2024-01-03"])
        return pd.Series([1.0, 2.0], index=index, name=ticker)


async def make_dataset(session, *, project_id, name, rows, created_at=None) -> Dataset:
    dataset = Dataset(
        project_id=project_id,
        name=name,
        filename=name,
        blob_path=f"uploads/{name}",
        source_label=f"upload: {name} (ingested 2024-01-05)",
        fingerprint="a" * 64,
        rows=len(rows),
        column_roles={"date": "date"},
        **({"created_at": created_at} if created_at else {}),
    )
    session.add(dataset)
    await session.flush()
    session.add_all(
        Observation(
            dataset_id=dataset.id,
            ts=pd.Timestamp(ts, tz="UTC").to_pydatetime(),
            symbol=symbol,
            field="price",
            value=value,
        )
        for ts, symbol, value in rows
    )
    await session.flush()
    return dataset


async def make_project(session, name="Uploads") -> Project:
    project = Project(name=name)
    session.add(project)
    await session.flush()
    return project


LONDON = [("2024-01-02", "LONDON", 500.0), ("2024-01-03", "LONDON", 501.0)]


async def test_a_symbol_an_upload_carries_is_served_from_it(session):
    project = await make_project(session)
    await make_dataset(session, project_id=project.id, name="hpi.csv", rows=LONDON)
    market = FakeMarket({"GOOGL"})

    source = await build_project_source(session, project.id, market=market)
    series = await source.prices("LONDON", start=START, end=END)

    assert list(series) == [500.0, 501.0]
    # The market source was never asked: an upload-only run must not need it.
    assert market.asked == []


async def test_a_symbol_no_upload_carries_falls_through_to_the_market(session):
    project = await make_project(session)
    await make_dataset(session, project_id=project.id, name="hpi.csv", rows=LONDON)
    market = FakeMarket({"GOOGL"})

    source = await build_project_source(session, project.id, market=market)
    series = await source.prices("GOOGL", start=START, end=END)

    assert list(series) == [1.0, 2.0]
    assert market.asked == ["GOOGL"]


async def test_provenance_names_the_source_of_every_served_ticker(session):
    project = await make_project(session)
    await make_dataset(session, project_id=project.id, name="hpi.csv", rows=LONDON)
    market = FakeMarket({"GOOGL"})

    source = await build_project_source(session, project.id, market=market)
    await source.prices("LONDON", start=START, end=END)
    await source.prices("GOOGL", start=START, end=END)

    assert source.provenance == {
        "LONDON": "upload: hpi.csv (ingested 2024-01-05)",
        "GOOGL": "Fake Market (dividend-adjusted)",
    }


async def test_the_newest_dataset_wins_a_symbol_two_uploads_carry(session):
    project = await make_project(session)
    # `created_at` defaults to `func.now()`, which in Postgres is *transaction
    # start* — two datasets flushed in one transaction tie exactly. That is the
    # same trap that makes `Message.seq` exist. Two confirmations are two
    # requests in real use, so the test has to say so explicitly or it asserts
    # a coin flip that happens to land right today.
    await make_dataset(
        session, project_id=project.id, name="old.csv",
        rows=[("2024-01-02", "LONDON", 1.0)],
        created_at=datetime(2024, 1, 5, tzinfo=UTC),
    )
    await make_dataset(
        session, project_id=project.id, name="new.csv",
        rows=[("2024-01-02", "LONDON", 99.0)],
        created_at=datetime(2024, 2, 5, tzinfo=UTC),
    )

    source = await build_project_source(session, project.id, market=FakeMarket(set()))
    series = await source.prices("LONDON", start=START, end=END)

    assert list(series) == [99.0]


async def test_another_projects_upload_is_not_visible(session):
    mine = await make_project(session, name="Mine")
    theirs = await make_project(session, name="Theirs")
    await make_dataset(session, project_id=theirs.id, name="hpi.csv", rows=LONDON)
    market = FakeMarket(set())

    source = await build_project_source(session, mine.id, market=market)
    with pytest.raises(DataUnavailableError):
        await source.prices("LONDON", start=START, end=END)
    assert market.asked == ["LONDON"]


async def test_the_label_names_the_market_source_and_every_upload(session):
    project = await make_project(session)
    await make_dataset(session, project_id=project.id, name="hpi.csv", rows=LONDON)

    source = await build_project_source(session, project.id, market=FakeMarket(set()))

    assert source.label == (
        "Fake Market (dividend-adjusted)"
        " + 1 uploaded dataset (upload: hpi.csv (ingested 2024-01-05))"
    )


async def test_a_project_with_no_uploads_is_the_market_source_unchanged(session):
    project = await make_project(session)
    market = FakeMarket({"GOOGL"})

    source = await build_project_source(session, project.id, market=market)

    # Not merely equivalent — the same object, so a project that never uploaded
    # anything carries no wrapper, no query cost and no changed label.
    assert source is market
```

- [ ] **Step 2: Run the tests and watch them fail**

```bash
cd backend && uv run pytest tests/data/test_project_source.py -v
```

Expected: collection error, `ModuleNotFoundError: No module named
'econometrica.data.project_source'`.

- [ ] **Step 3: Implement**

Create `backend/src/econometrica/data/project_source.py`:

```python
"""A project's uploads, layered over whatever market source is configured.

Upload-first, and that ordering is the whole point: a run has to be able to mix
a file with fetched tickers, because the question this exists to answer
(correlate an uploaded index against a listed stock) needs both in one frame.
An all-or-nothing switch between market data and an upload would have been a
smaller change and could not answer it.

The composite records which source served each ticker, because only it knows.
`DataSteward` reads `provenance` back off it the same duck-typed way it already
reads `label`, so an ordinary source needs no changes to keep working.
"""

from datetime import date
from uuid import UUID

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from econometrica.data.base import PriceSource
from econometrica.data.uploaded import UploadedPriceSource
from econometrica.db.models import Dataset, Observation


class ProjectPriceSource:
    """Serves a project's uploaded symbols; delegates the rest."""

    def __init__(
        self,
        session: AsyncSession,
        market: PriceSource,
        *,
        uploads: dict[str, tuple[UUID, str]],
        label: str,
    ) -> None:
        self._session = session
        self._market = market
        self._uploads = uploads
        self._label = label
        self._provenance: dict[str, str] = {}

    @property
    def label(self) -> str:
        return self._label

    @property
    def provenance(self) -> dict[str, str]:
        """Which source served each ticker, filled in as they are served.

        A copy, because the Data Steward reads this after fetching and a
        mutable view of a live dict would let a later fetch rewrite a report
        that had already been built.
        """
        return dict(self._provenance)

    async def prices(self, ticker: str, *, start: date, end: date) -> pd.Series:
        entry = self._uploads.get(ticker)
        if entry is None:
            series = await self._market.prices(ticker, start=start, end=end)
            self._provenance[ticker] = _label_of(self._market)
            return series

        dataset_id, source_label = entry
        series = await UploadedPriceSource(
            self._session, dataset_id, label=source_label
        ).prices(ticker, start=start, end=end)
        self._provenance[ticker] = source_label
        return series


async def build_project_source(
    session: AsyncSession, project_id: UUID, *, market: PriceSource
) -> PriceSource:
    """Wrap ``market`` with the project's uploads, if it has any.

    One query, at construction, because `PriceSource.label` is a synchronous
    property and has to be composed before anything is fetched.

    `observations` carries no `project_id` — unlike `document_chunks`, which
    denormalises it — so this joins. That is affordable *here* because it is the
    only such query and it runs once per run; a second query site is the signal
    to denormalise instead.
    """
    rows = (
        await session.execute(
            select(Observation.symbol, Dataset.id, Dataset.source_label)
            .join(Dataset, Dataset.id == Observation.dataset_id)
            .where(Dataset.project_id == project_id)
            .distinct()
            .order_by(Dataset.created_at)
        )
    ).all()

    # Later rows overwrite earlier ones, so the most recently ingested dataset
    # wins a symbol two uploads both carry. Explicable to a user: the last file
    # you confirmed is the one in force.
    #
    # `created_at` is `func.now()`, which is *transaction start* — two datasets
    # written in one transaction tie exactly, the same reason `Message.seq`
    # exists. Two confirmations are two requests, so this is well-ordered in
    # use; only a test writing both at once has to set the column itself.
    uploads: dict[str, tuple[UUID, str]] = {
        symbol: (dataset_id, source_label) for symbol, dataset_id, source_label in rows
    }
    if not uploads:
        # No wrapper at all rather than one that always delegates: a project
        # that never uploaded anything should not have its label changed or its
        # provenance described.
        return market

    labels = sorted({source_label for _, source_label in uploads.values()})
    noun = "dataset" if len(labels) == 1 else "datasets"
    return ProjectPriceSource(
        session,
        market,
        uploads=uploads,
        label=f"{_label_of(market)} + {len(labels)} uploaded {noun} ({', '.join(labels)})",
    )


def _label_of(source: PriceSource) -> str:
    """The same fallback `DataSteward` uses, so the two never disagree."""
    return getattr(source, "label", "") or type(source).__name__
```

- [ ] **Step 4: Run the tests and watch them pass**

```bash
cd backend && uv run pytest tests/data/test_project_source.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Check the layering rule still holds**

```bash
cd backend && uv run pytest tests/data/test_layering.py -v
```

Expected: pass. `project_source.py` imports `db.models`, which `uploaded.py`
already does; it must not import anything under `agents/`.

- [ ] **Step 6: Lint and type-check**

```bash
cd backend && uv run ruff check src tests && uv run mypy src
```

- [ ] **Step 7: Commit**

```bash
git add backend/src/econometrica/data/project_source.py backend/tests/data/test_project_source.py
git commit -F <message file>
```

Message subject: `feat(data): serve a project's uploads ahead of the market source`.
Body: why upload-first (mixing in one frame), why the factory is async (label is
a sync property), why a project with no uploads gets the market source back
unwrapped, and why the symbol map is a join rather than a denormalised column.

---

### Task 2: `source_label` refuses a filename that collides with the synthetic seam

**Files:**
- Modify: `backend/src/econometrica/services/datasets.py:25-33`
- Test: `backend/tests/services/test_datasets.py`

**Interfaces:**
- Consumes: `MappingError` from `services/mapping.py`, already imported by
  `datasets.py`.
- Produces: `source_label` may now raise `MappingError`. The confirm route maps
  it to 422 at `api/routers/uploads.py:159`, so no route change is needed.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/services/test_datasets.py` (create the file with this
content if it does not exist):

```python
from datetime import UTC, datetime

import pytest

from econometrica.services.datasets import source_label
from econometrica.services.mapping import MappingError


def test_a_label_names_the_file_and_when_it_was_ingested():
    label = source_label("prices.csv", datetime(2024, 1, 5, tzinfo=UTC))
    assert label == "upload: prices.csv (ingested 2024-01-05)"


def test_a_filename_containing_synthetic_is_refused():
    """`DataQualityReport.source` flags generated data by substring.

    Once an upload's label is composed into that string, a file named this way
    would make a run on real data announce that its prices were generated. The
    filename is the one input a user controls, so it is the one that has to be
    refused.
    """
    with pytest.raises(MappingError) as exc:
        source_label("synthetic-test.csv", datetime(2024, 1, 5, tzinfo=UTC))

    assert "synthetic" in str(exc.value)
    assert "rename" in str(exc.value).lower()


def test_the_check_is_case_insensitive():
    with pytest.raises(MappingError):
        source_label("Synthetic_Prices.CSV", datetime(2024, 1, 5, tzinfo=UTC))
```

- [ ] **Step 2: Run the tests and watch the new ones fail**

```bash
cd backend && uv run pytest tests/services/test_datasets.py -v
```

Expected: the first test passes; the other two fail with
`DID NOT RAISE <class 'MappingError'>`.

- [ ] **Step 3: Implement**

Replace `source_label` in `backend/src/econometrica/services/datasets.py`:

```python
def source_label(filename: str, when: datetime | None = None) -> str:
    """What reaches `DataQualityReport.source`.

    Names the file *and* when it was ingested, because a file re-uploaded after
    being corrected is a different series under the same name — the same reason
    a market adapter names its adjustment policy.

    Refuses a filename carrying the word `synthetic`. `DataSteward.resolve`
    raises its `synthetic_data` risk flag on a substring of the source string,
    and this label is composed into that string for a run that reads an upload
    — so such a file would make real data announce itself as generated. Refused
    rather than rewritten: this label is provenance, and quietly editing the
    record of where a number came from is the worse failure, because it is
    invisible.
    """
    if "synthetic" in filename.lower():
        raise MappingError(
            f"{filename!r} cannot be ingested: a source label carrying the word"
            " 'synthetic' is how this application marks generated data, and a"
            " run reading this file would report real observations as generated."
            " Rename the file and upload it again"
        )
    stamp = (when or datetime.now(UTC)).date()
    return f"upload: {filename} (ingested {stamp})"
```

- [ ] **Step 4: Run the tests and watch them pass**

```bash
cd backend && uv run pytest tests/services/test_datasets.py tests/services/test_ingest.py -v
```

Expected: all pass. `test_ingest.py` is included because it exercises
`ingest_observations`, which calls this.

- [ ] **Step 5: Commit**

```bash
git add backend/src/econometrica/services/datasets.py backend/tests/services/test_datasets.py
git commit -F <message file>
```

Message subject: `fix(uploads): refuse a filename that collides with the synthetic flag`.
Body: the substring seam, why the filename is the input that matters, and why
refusing beats rewriting.

---

### Task 3: `DataSteward` discloses mixed provenance

**Files:**
- Modify: `backend/src/econometrica/agents/data_steward.py:199-220`
- Test: `backend/tests/agents/test_data_steward.py`

**Interfaces:**
- Consumes: `ProjectPriceSource.provenance` from Task 1 — but read via
  `getattr`, so nothing imports it.
- Produces: a `QualityFlag` with `code="mixed_sources"`, `severity="info"`.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/agents/test_data_steward.py`. Follow the fake-source
pattern already used in that file for the other flags:

```python
class TwoSourceStub:
    """Stands in for `ProjectPriceSource`: serves anything, records provenance."""

    label = "Fake Market + 1 uploaded dataset (upload: hpi.csv (ingested 2024-01-05))"

    def __init__(self, provenance: dict[str, str]) -> None:
        self._provenance = provenance

    @property
    def provenance(self) -> dict[str, str]:
        return dict(self._provenance)

    async def prices(self, ticker, *, start, end):
        index = pd.bdate_range("2024-01-01", periods=60)
        return pd.Series(range(1, 61), index=index, dtype=float, name=ticker)


async def test_a_run_drawing_on_two_sources_says_which_served_what():
    source = TwoSourceStub(
        {
            "LONDON": "upload: hpi.csv (ingested 2024-01-05)",
            "GOOGL": "Fake Market",
        }
    )
    dataset = await DataSteward(source).resolve(
        DatasetSpec(
            tickers=["LONDON", "GOOGL"],
            start=date(2024, 1, 1),
            end=date(2024, 3, 1),
            frequency="D",
        )
    )

    flag = dataset.quality.flag("mixed_sources")
    assert flag.severity == "info"
    assert "LONDON from upload: hpi.csv (ingested 2024-01-05)" in flag.detail
    assert "GOOGL from Fake Market" in flag.detail


async def test_no_flag_when_every_ticker_came_from_one_source():
    source = TwoSourceStub({"AAPL": "Fake Market", "MSFT": "Fake Market"})
    dataset = await DataSteward(source).resolve(
        DatasetSpec(
            tickers=["AAPL", "MSFT"],
            start=date(2024, 1, 1),
            end=date(2024, 3, 1),
            frequency="D",
        )
    )

    assert not dataset.quality.has("mixed_sources")


async def test_synthetic_data_still_fires_when_the_market_source_is_generated():
    """The seam this change runs closest to, pinned from both sides."""
    source = TwoSourceStub({"AAPL": "Synthetic (generated, not market data)"})
    source.label = "Synthetic (generated, not market data)"
    dataset = await DataSteward(source).resolve(
        DatasetSpec(
            tickers=["AAPL"],
            start=date(2024, 1, 1),
            end=date(2024, 3, 1),
            frequency="D",
        )
    )

    assert dataset.quality.has("synthetic_data")
    assert dataset.quality.flag("synthetic_data").severity == "risk"


async def test_an_upload_named_after_a_real_file_does_not_read_as_generated():
    """The `synthetic_data` seam must not fire on ordinary uploaded data."""
    source = TwoSourceStub({"LONDON": "upload: hpi.csv (ingested 2024-01-05)"})
    dataset = await DataSteward(source).resolve(
        DatasetSpec(
            tickers=["LONDON"],
            start=date(2024, 1, 1),
            end=date(2024, 3, 1),
            frequency="D",
        )
    )

    assert not dataset.quality.has("synthetic_data")
```

- [ ] **Step 2: Run the tests and watch the first fail**

```bash
cd backend && uv run pytest tests/agents/test_data_steward.py -k "mixed_sources or one_source or generated" -v
```

Expected: `test_a_run_drawing_on_two_sources_says_which_served_what` fails with
`KeyError: "no flag 'mixed_sources' in this report"`. The other two pass
already — they are regression guards, and a test that passes first time is
still worth having when it pins a seam this sharp.

- [ ] **Step 3: Implement**

In `resolve`, after `raw = await self._fetch(spec)` and after the existing
`synthetic_data` block, add:

```python
        # Read back off the source rather than tracked here: only a composite
        # knows which of its members answered. `getattr` for the same reason
        # `label` uses it above — an ordinary source has no such property and
        # must not need one.
        provenance: dict[str, str] = getattr(self.source, "provenance", {})
        if len(set(provenance.values())) > 1:
            by_source: dict[str, list[str]] = {}
            for ticker, served_by in provenance.items():
                by_source.setdefault(served_by, []).append(ticker)
            detail = "; ".join(
                f"{', '.join(sorted(tickers))} from {served_by}"
                for served_by, tickers in sorted(by_source.items())
            )
            flags.append(
                QualityFlag(
                    code="mixed_sources",
                    severity="info",
                    # Info, not warning: mixing is the feature, and the reader
                    # needs to know the split rather than be warned off it.
                    detail=f"this frame was assembled from more than one source — {detail}",
                )
            )
```

Note the ordering: `provenance` is only populated by `_fetch`, so this block
must come after it. `source` above it is read before the fetch and stays that
way.

- [ ] **Step 4: Run the tests and watch them pass**

```bash
cd backend && uv run pytest tests/agents/test_data_steward.py -v
```

Expected: all pass, including the pre-existing flag tests.

- [ ] **Step 5: Lint and type-check, then commit**

```bash
cd backend && uv run ruff check src tests && uv run mypy src
git add backend/src/econometrica/agents/data_steward.py backend/tests/agents/test_data_steward.py
git commit -F <message file>
```

Message subject: `feat(data): disclose which source served each ticker`.
Body: why the label alone cannot describe a mixed run (it is read before the
fetch), why the flag is `info`, and why `getattr` rather than a type import.

---

### Task 4: a run reads the project's uploads

**Files:**
- Modify: `backend/src/econometrica/api/routers/runs.py:206-220` (`start_run`)
  and `:263-301` (`_build`)
- Test: `backend/tests/api/test_runs.py`

**Interfaces:**
- Consumes: `build_project_source` from Task 1.
- Produces: `_build` becomes `async def _build(project, chat, registry, source,
  rate_source, factor_source, session)` and is awaited.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/api/test_runs.py`. The existing `make_chat(client)` makes
its own project and returns only the chat id, so this needs a variant that
returns both; the rest reuses `ScriptedRegistry`, `NARRATIVE` and `events`,
which are already there.

```python
UPLOAD_PLAN = {
    "question": "Describe LONDON",
    "dataset": {
        "tickers": ["LONDON"],
        "start": "2024-01-01",
        "end": "2024-06-30",
        "frequency": "D",
        "return_method": "log",
        "risk_free": None,
        "factors": None,
    },
    "steps": [
        {
            "id": "s1",
            "tool": "adf",
            "params": {"column": "LONDON"},
            "depends_on": [],
            "rationale": "stationarity of the uploaded level",
        }
    ],
    "hypotheses": [],
    "chart_intents": [],
}


class EmptyMarket:
    """Knows nothing.

    Deliberately not a source that happens to lack LONDON: if a run resolves
    against this, it resolved from the upload and there is no other reading.
    """

    label = "Fake Market (dividend-adjusted)"

    async def prices(self, ticker: str, *, start: date, end: date) -> pd.Series:
        raise DataUnavailableError(f"{ticker}: not listed")


@pytest_asyncio.fixture
async def uploading():
    registry = ScriptedRegistry([json.dumps(UPLOAD_PLAN), NARRATIVE])
    app.dependency_overrides[get_provider_registry] = lambda: registry
    app.dependency_overrides[get_price_source] = lambda: EmptyMarket()
    yield registry
    app.dependency_overrides.pop(get_provider_registry, None)
    app.dependency_overrides.pop(get_price_source, None)


async def make_project_and_chat(client) -> tuple[str, str]:
    """As `make_chat`, but hands back the project id the upload needs."""
    project = (await client.post("/api/projects", json={"name": "Uploads"})).json()
    await client.patch(
        f"/api/projects/{project['id']}",
        json={
            "validation_tier": "single",
            "model_assignments": {
                "planner": {"provider": "ollama", "model": "fake-1"},
                "narrator": {"provider": "ollama", "model": "fake-1"},
            },
        },
    )
    chat = (
        await client.post(f"/api/projects/{project['id']}/chats", json={"name": "c"})
    ).json()
    return str(project["id"]), str(chat["id"])


async def ingest_london(session, project_id: str):
    """180 daily levels under the symbol LONDON, written the way ingest does."""
    from uuid import UUID

    from econometrica.db.models import Dataset, Observation

    dataset = Dataset(
        project_id=UUID(project_id),
        name="hpi.csv",
        filename="hpi.csv",
        blob_path="uploads/hpi.csv",
        source_label="upload: hpi.csv (ingested 2024-01-05)",
        fingerprint="a" * 64,
        rows=180,
        column_roles={"date": "date", "LONDON": "price"},
    )
    session.add(dataset)
    await session.flush()

    days = pd.date_range("2024-01-01", periods=180, freq="D")
    rng = np.random.default_rng(11)
    values = 500.0 + np.cumsum(rng.normal(size=180))
    session.add_all(
        Observation(
            dataset_id=dataset.id,
            ts=day.tz_localize("UTC").to_pydatetime(),
            symbol="LONDON",
            field="price",
            value=float(value),
        )
        for day, value in zip(days, values, strict=True)
    )
    await session.flush()
    return dataset


async def test_a_run_resolves_a_symbol_from_the_projects_upload(client, session, uploading):
    """The end the whole upload path was built for.

    Asserted through the route rather than against the steward, because the
    wiring is the thing that was missing — every piece below it already worked.
    """
    project_id, chat_id = await make_project_and_chat(client)
    await ingest_london(session, project_id)

    async with client.stream(
        "POST", f"/api/chats/{chat_id}/runs", json={"question": "Describe LONDON"}
    ) as response:
        body = "".join([chunk async for chunk in response.aiter_text()])

    finished = [e for e in events(body) if e["event"] == "run.finished"]
    assert finished, body
    quality = finished[0]["data"]["payload"]["dataset"]["quality"]
    assert quality["tickers"] == ["LONDON"]
    assert "upload: hpi.csv" in quality["source"]
```

`DataUnavailableError`, `np` and `pytest_asyncio` are already imported in that
file; add `from econometrica.data.base import DataUnavailableError` if it is
not.

- [ ] **Step 2: Run it and watch it fail**

```bash
cd backend && uv run pytest tests/api/test_runs.py -k upload -v
```

Expected: `DataUnavailableError: LONDON: ...` — the configured test source has
no such symbol, which is precisely the gap being closed.

- [ ] **Step 3: Implement**

In `runs.py`, add the import:

```python
from econometrica.data.project_source import build_project_source
```

Change `_build`'s signature to take the session and to be async, and compose
the source before constructing the steward:

```python
async def _build(
    project: Project,
    chat: Chat,
    registry: ProviderRegistry,
    source: PriceSourceDep,
    rate_source: PriceSourceDep,
    factor_source: FactorSourceDep,
    session: AsyncSession,
) -> Orchestrator:
    ...
    # The project's own uploads take precedence over the configured market
    # source, so one run can mix a file with fetched tickers. A project with no
    # uploads gets `source` back unchanged.
    prices = await build_project_source(session, project.id, market=source)
    ...
        steward=DataSteward(
            prices, rate_source=rate_source, factor_source=factor_source
        ),
```

At the call site in `start_run`:

```python
    orchestrator = await _build(
        project, chat, registry, source, rate_source, factor_source, session
    )
```

- [ ] **Step 4: Run the tests and watch them pass**

```bash
cd backend && uv run pytest tests/api/test_runs.py -v
```

Expected: all pass. Every other caller of `_build` must be awaited — Task 5
handles `rerun`, which does not currently call it.

- [ ] **Step 5: Lint, type-check, commit**

```bash
cd backend && uv run ruff check src tests && uv run mypy src
git add backend/src/econometrica/api/routers/runs.py backend/tests/api/test_runs.py
git commit -F <message file>
```

Message subject: `feat(runs): let a run read the project's uploaded data`.
Body: the wiring was the only missing piece; note that `deps.py` and
`data/registry.py` are deliberately untouched.

---

### Task 5: re-run reproduces from the upload, not from the market

**Files:**
- Modify: `backend/src/econometrica/api/routers/runs.py:87-141` (`rerun`)
- Test: `backend/tests/api/test_runs.py`

**Interfaces:**
- Consumes: `build_project_source` from Task 1; `get_chat_or_404` and
  `get_project_or_404`, already imported in this module.
- Produces: no new public names.

- [ ] **Step 1: Write the failing tests**

Reuses `uploading`, `make_project_and_chat` and `ingest_london` from Task 4.

```python
async def start_upload_run(client, session) -> tuple[str, str]:
    """Run once against an uploaded series and hand back the ids."""
    project_id, chat_id = await make_project_and_chat(client)
    await ingest_london(session, project_id)

    async with client.stream(
        "POST", f"/api/chats/{chat_id}/runs", json={"question": "Describe LONDON"}
    ) as response:
        body = "".join([chunk async for chunk in response.aiter_text()])

    runs = (await client.get(f"/api/chats/{chat_id}/runs")).json()
    return project_id, str(runs[0]["id"])


async def test_rerun_reproduces_an_uploaded_series_from_the_upload(
    client, session, uploading
):
    """The manifest claim, at the point it would silently break.

    `rerun` took the globally configured source and never looked at the
    project, so a run built on a file re-resolved from the market source and
    reported on data the original never saw. Here the market source cannot
    serve LONDON at all, so a 200 is only reachable through the upload.
    """
    _, run_id = await start_upload_run(client, session)

    response = await client.post(f"/api/runs/{run_id}/rerun")

    assert response.status_code == 200, response.text
    assert response.json()["reproduced"] is True


async def test_rerun_answers_409_when_the_dataset_is_gone(client, session, uploading):
    from sqlalchemy import delete

    from econometrica.db.models import Dataset

    _, run_id = await start_upload_run(client, session)
    await session.execute(delete(Dataset))
    await session.flush()

    response = await client.post(f"/api/runs/{run_id}/rerun")

    # A run whose data is gone is a finding, not a crash.
    assert response.status_code == 409
    assert "LONDON" in response.json()["detail"]
```

- [ ] **Step 2: Run them and watch them fail**

```bash
cd backend && uv run pytest tests/api/test_runs.py -k rerun -v
```

Expected: the first fails with a `DataUnavailableError` escaping as a 500,
because `rerun` resolves against the configured market source, which has no
`LONDON`. The second fails the same way — a 500 rather than a 409.

- [ ] **Step 3: Implement**

In `rerun`, after loading the run and before resolving, scope the source to the
run's project and turn an unavailable dataset into a 409:

```python
    chat = await get_chat_or_404(session, run.chat_id)
    project = await get_project_or_404(session, chat.project_id)
    # Scoped to the project for the same reason the run itself is: a manifest
    # that reproduces from a different source than it was built on has not
    # reproduced anything.
    prices = await build_project_source(session, project.id, market=source)

    try:
        dataset = await DataSteward(
            prices, rate_source=rate_source, factor_source=factor_source
        ).resolve(plan.dataset)
    except DataUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"this run cannot be reproduced: {exc}. The data it was built on"
                " is no longer available — an uploaded dataset may have been"
                " deleted"
            ),
        ) from exc
    execution = await Econometrician().run(plan, dataset.frame)
```

Add `DataUnavailableError` to the imports from `econometrica.data.base` if it is
not already there.

- [ ] **Step 4: Run the tests and watch them pass**

```bash
cd backend && uv run pytest tests/api/test_runs.py -v
```

- [ ] **Step 5: Run the whole backend suite**

```bash
cd backend && uv run pytest -q
```

Expected: 1407 + the new tests, all passing.

- [ ] **Step 6: Lint, type-check, commit**

```bash
cd backend && uv run ruff check src tests alembic && uv run mypy src
git add backend/src/econometrica/api/routers/runs.py backend/tests/api/test_runs.py
git commit -F <message file>
```

Message subject: `fix(runs): re-run an uploaded series against its upload`.
Body: the silent-reproduction hole and why a missing dataset is a 409.

---

### Task 6: documentation

**Files:**
- Modify: `CLAUDE.md`, `README.md`

- [ ] **Step 1: Update `CLAUDE.md`**

Replace the paragraph claiming uploads work end to end with what is now true:
a run reads a project's uploads first and falls through to the market source;
`mixed_sources` names the split; `build_project_source` returns the market
source unwrapped when a project has none; and a filename carrying "synthetic"
is refused at confirm time, with the reason.

Also correct the standing claim that `UploadedPriceSource` "serves it through
the same `PriceSource` protocol" — true of the class since Phase 6, but nothing
constructed one until now.

- [ ] **Step 2: Update `README.md`**

In the uploads paragraph, say that an uploaded series can be analysed alongside
fetched tickers in one run, and that every run naming more than one source says
which series came from where.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md README.md
git commit -F <message file>
```

Message subject: `docs: record that a run can read an upload`.

---

## Verification

The whole gate, from a clean tree:

```bash
cd backend && uv run pytest -q && uv run ruff check src tests alembic && uv run mypy src
```

```bash
cd frontend && npx vitest run && npx tsc --noEmit
```

The frontend is unchanged by this plan; run it anyway, because
`DataQualityReport` is serialised into the stored outcome and the canvas reads
`quality.source`.

Then the thing the plan is actually for, by hand: start the stack with
`.\start.ps1`, upload a two-column CSV of a London house-price index, confirm
the mapping, and run **"Compare GOOGL and LONDON monthly log returns from
2015-01-01 to 2024-01-01 with capm and granger causality."** The run should
complete, the canvas should show a `mixed_sources` info flag naming both
sources, and re-running it should reproduce.
