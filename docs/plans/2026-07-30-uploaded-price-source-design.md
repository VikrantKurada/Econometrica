# Uploaded data in a run: design note

**Date:** 2026-07-30
**Status:** approved, not implemented

An uploaded dataset can be profiled, mapped, confirmed and stored, and
`UploadedPriceSource` can serve it through the `PriceSource` protocol. Nothing
constructs one. This note says how a run gets to read it.

---

## What is actually missing

`UploadedPriceSource` is imported by `tests/data/test_uploaded.py` and by
nothing else. `api/deps.py::get_price_source` builds one source from the single
global `ECONOMETRICA_PRICE_SOURCE` setting, with no project context and no way
to name a dataset, so a run can never reach an upload however it is ingested.

The ingest half works: `POST /api/projects/{id}/uploads` profiles,
`POST /api/uploads/{id}/confirm` validates and stores, and the observations
land in the hypertable. The gap is the last hop.

**This was found chasing a real question** — "correlate London real estate
against Google" — which fails today because Yahoo has no house-price index at
any symbol and there is no other way to get one in. That question also needs
both series in *one* frame, which is what settles the shape below.

---

## The question that decides the shape

A run must be able to mix. GOOGL comes from Yahoo, a UK HPI series comes from a
file, and they have to align into one `Dataset` or the analysis cannot happen.
An all-or-nothing switch between "market data" and "my upload" would be a
smaller change and would not answer the question that motivated it.

So: **upload-first fallback**, per project. If any dataset in the project
carries the symbol, it is served from there; everything else falls through to
the configured market source. No new UI, no per-run selection, and the mixing
case works by construction.

---

## The shape

A new `data/project_source.py`:

```python
class ProjectPriceSource:          # satisfies PriceSource
    label: str                      # composed at construction
    async def prices(ticker, *, start, end) -> pd.Series
```

built by an async factory, because the symbol map needs a query and `label` is
a synchronous property:

```python
async def build_project_source(session, project_id, *, market: PriceSource) -> PriceSource
```

`runs.py::_build` becomes `async` and calls it; `rerun` calls it too (see
below). `api/deps.py` and `data/registry.py` are untouched — the registry is
the set of *configurable global* sources, and this is a run-scoped wrapper over
whichever one is configured. `data/` importing `db.models` is already
established by `uploaded.py`, so `tests/data/test_layering.py` still holds.

### The symbol map

One query at construction: distinct `(symbol, dataset_id)` over `observations`
joined to `datasets`, filtered by `project_id`, ordered so the most recently
created dataset wins a collision between two uploads. The result is a dict, and
the source keeps it for the run's lifetime.

`observations` carries no `project_id`, so this is a join — unlike
`document_chunks`, which denormalises the column precisely so a query filters
on the row it reads. The join is the right call *here* because it exists in
exactly one function and runs once per run. A second query site is the trigger
to reconsider; adding a column to a hypertable is a migration this does not
need yet.

### How the steward learns what happened

The flags below are raised by `DataSteward`, but only the composite knows which
source served which ticker. It exposes two read-only properties — `provenance`
(ticker to source label, populated as it serves) and `shadowed` (symbols that
existed in both) — and the steward reads them **the same duck-typed way it
already reads `label`**:

```python
source = getattr(self.source, "label", "") or type(self.source).__name__
provenance = getattr(self.source, "provenance", {})
```

So an ordinary `PriceSource` needs no changes to keep working, and the steward
does not import the composite's type. This is the existing pattern at
`agents/data_steward.py:203`, not a new one.

### Session lifetime

`UploadedPriceSource` needs a live `AsyncSession`. A run executes inside the
SSE generator in `start_run`, which already calls `record_run` and
`session.commit()` after streaming — so the session demonstrably outlives the
run and there is nothing to arrange.

---

## Provenance

`DataSteward.resolve` reads `self.source.label` **once, before fetching**, into
`DataQualityReport.source`. A mixed run therefore cannot describe itself in
that one string alone, and two channels are used instead. They answer different
questions and the difference is the point.

**`label` — what was available.** Composed at construction, because that is all
that is known before a plan exists:

```
Yahoo Finance (dividend-adjusted) + 1 uploaded dataset (uk-hpi.csv, ingested 2026-07-30)
```

**`mixed_sources` — what was used.** An `info` flag raised after the fetch,
only when a run actually drew from more than one source, and authoritative:

```
LONDON from uk-hpi.csv (ingested 2026-07-30);
GOOGL, ^GSPC from Yahoo Finance (dividend-adjusted)
```

This mirrors `mixed_risk_free`, which honours a request and discloses rather
than refusing it.

**Known imprecision, recorded rather than hidden.** A project that has uploads
but whose run touches none of them still reads `+ 1 uploaded dataset` in its
label, with no flag beside it. Composing the label after the fetch would fix
it, and would also give the cache a channel to disclose staleness it currently
lacks — but it moves an ordering the rest of the system is written against, so
it is deferred and not forgotten.

### Shadowing

A symbol carried by both an upload and the market source is served from the
upload, with a `shadowed_symbol` **warning** flag naming the symbol and both
sources. A user who uploaded their own AAPL history probably meant to use it;
what must not happen is discovering the substitution by noticing the numbers
look wrong.

---

## The hazard this introduces

`DataSteward.resolve` raises `synthetic_data` on `"synthetic" in
source.lower()`. `Dataset.source_label` is built from a **user-supplied
filename**, and today it never reaches `DataQualityReport.source`. Composing it
in means a file named `synthetic-test.csv` would make a run on real uploaded
data announce that its prices were generated.

So `services/ingest.py` **rejects** it: a file whose name would put "synthetic"
into `source_label` is refused at confirm time, with a message saying why and
naming the remedy (rename the file and re-upload). Rejecting rather than
quietly rewriting, because `source_label` is provenance — silently editing the
record of where a number came from is a worse failure than refusing a filename,
and it would be invisible.

The existing rule — no real adapter's label may contain the word, one test per
source — was written about adapters, and a filename is not an adapter. This is
the same rule reaching the one input a user controls.

---

## Re-run

`rerun` takes the global `PriceSourceDep` and never looks at the project, so a
run built on an upload would silently re-resolve from Yahoo and report on data
the original never saw. That is the manifest claim failing exactly where it
matters most, so it is part of this change and not a follow-up.

It gains the `run -> chat -> project` lookup and builds the same composite. If
the dataset has been deleted since, `resolve` raises `DataUnavailableError`;
the route turns that into a **409 naming the dataset**, not a 500. A run whose
data is gone is a finding, not a crash.

---

## Tests, written first

Per the project's TDD rule: each is written, run, and watched to fail with the
expected error before the code exists.

**`ProjectPriceSource`**

- a symbol the upload carries is served from it
- a symbol it does not carry falls through to the market source
- a symbol in both is served from the upload *and* raises `shadowed_symbol`
- two uploads carrying one symbol: the most recent dataset wins
- the composed label names the market source and every available upload

**`DataSteward`**

- `mixed_sources` detail lists each ticker under the source that served it
- the flag is absent when every ticker came from one source
- `synthetic_data` still fires when the market source is the synthetic one
- a dataset whose filename contains "synthetic" does **not** fire it

**Routes**

- a plan naming an uploaded symbol resolves end to end through `start_run`
- `rerun` on an uploaded run reproduces from the upload, not from the market
  source — the regression that motivates the re-run change
- `rerun` on a run whose dataset was deleted answers 409, not 500

**Layering**

- `tests/data/test_layering.py` still passes, including its subprocess check of
  both import orders

---

## What this deliberately does not do

- **No dataset picker in the UI.** Upload-first needs none; adding one would be
  a second way to express the same thing.
- **No uploads serving risk-free rates or factor sets.** Those resolve through
  their own sources for stated reasons — Yahoo has no `DGS3MO`, and a factor
  set is one object rather than a series.
- **Does not move `ColumnMapping` out of `/gallery.html`.** Still open, still
  waiting on where "Data" lives in the three-pane layout.
- **Does not touch the web-search, retrieval or MCP wiring gaps.** Those three
  are unreached for the same structural reason and are separate work.

---

## Size

One new module, three touched (`api/routers/runs.py`,
`agents/data_steward.py`, `services/ingest.py`), no migration, about ten new
tests.
