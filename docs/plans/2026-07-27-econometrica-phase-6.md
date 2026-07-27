# Phase 6: Platform — step-level plan

> **For Claude:** the parent plan is
> `docs/plans/2026-07-24-econometrica-implementation.md` (Phase 6 table); the
> design rationale is `docs/plans/2026-07-24-econometrica-design.md` §3 (market
> data), §8 (telemetry) and §9 (files, web search, MCP). Phase 4's and Phase 5's
> step-level plans are the format precedent.

**Goal:** the workbench stops depending on generated data. Real prices, real
factors and real uploads flow through the pipeline everything above the
`PriceSource` protocol was already tested against; every run becomes
inspectable in a trace viewer with its cost and latency; and the two
integration surfaces the design promises — MCP and retrieval — arrive behind
allowlists that are off by default.

---

## Progress

| Task | State |
|---|---|
| 6.1 yfinance `PriceSource` | ✅ |
| 6.2 source registry, disk cache, offline failures | ✅ |
| 6.3 FRED adapter and the dead `risk_free` field | ✅ |
| 6.4 Ken French factors — unlocking `ff3`/`ff5`/`carhart4` | ✅ |
| 6.5 grounding gate: the `(s3)` false positive | ✅ |
| 6.6 upload profiling and schema inference | ✅ |
| 6.7 column-role mapping the user confirms | ✅ |
| 6.8 dataset store — hypertable plus retained blob | ✅ |
| 6.9 telemetry — spans to Postgres, OTLP, metrics | ⬜ |
| 6.10 trace viewer and cost dashboard | ⬜ |
| 6.11 MCP client with an allowlist | ⬜ |
| 6.12 project-scoped retrieval over pgvector | ⬜ |
| 6.13 web search, off by default, attributed | ⬜ |
| 6.14 PDF export — print stylesheet, no new dependency | ⬜ |
| 6.15 sandboxed code escape hatch | ⬜ |
| 6.16 Phase 6 e2e — full regression | ⬜ |

---

## What Phase 6 inherits

Verified in the tree at `7083692`. The seam is already cut and already tested:

| Already true | Consequence for this phase |
|---|---|
| `PriceSource` is a `Protocol` in `agents/data_steward.py` with a `label` and one `async prices(ticker, *, start, end) -> pd.Series` | a real adapter is a new class, not a refactor |
| `DataQualityReport.source` exists and is populated from `PriceSource.label` | attribution needs no schema change |
| `api/deps.py:get_price_source` is the single selection point, overridable in tests | one function changes, ~918 tests keep passing |
| `_UnconfiguredPriceSource` refuses with prose rather than returning empty frames | the "no adapter" path is already honest |
| Anything whose label contains `synthetic` gets a `risk` flag the canvas cannot tab away | the honesty seam is built; real sources must not accidentally trip it |
| `Dataset.frame` concatenates levels with `_return`-suffixed returns | a factor column arriving from a *non-price* source has to decide which half it belongs to — see decision 4 |

Two gaps found while reading, both real and both closed by this phase:

- **`DatasetSpec.risk_free` is dead.** The field exists, `agents/planner.py`
  shows it to the model in the example JSON, and `DataSteward.resolve` iterates
  `spec.tickers` only. A Planner that sets it gets it silently dropped — the
  exact failure `PlanStep`'s unknown-parameter check exists to prevent, one
  layer up. Task 6.3.
- **`ff3`, `ff5` and `carhart4` can never run.** They are in the catalogue every
  Planner reads and their params default to `["mkt_rf","smb","hml"]`, but no
  source can produce a factor column, so `require_columns` raises and the step
  lands as `failed`. Three of thirty-seven tools are unreachable through the
  pipeline. Task 6.4.

---

## What the machine actually does — live probes, 2026-07-27

Run before writing this plan, against the real services, because the parent
plan's version floors are two years stale. **Four of its five assumptions about
market data are wrong.**

| The parent plan assumes | What actually happens |
|---|---|
| `yfinance>=0.2.50` | resolves to **1.5.2**. `auto_adjust=True` is the default and there is *no* `Adj Close` column unless you pass `auto_adjust=False` |
| a bad ticker raises | `yf.download("NOTATICKERXYZ", …)` returns an **empty DataFrame**, shape `(0, 6)`, and logs `possibly delisted` to stderr. Nothing raises |
| columns are flat | columns are a `MultiIndex(names=["Price","Ticker"])` even for one ticker, unless `multi_level_index=False` |
| **Stooq via `pandas-datareader`** | `pandas-datareader` resolved to **0.11.1**, which implements only `bankofcanada`, `econdb`, `eurostat`, `famafrench`, `fred`, `oecd`. `DataReader(..., "stooq")` raises `NotImplementedError`. And the CSV endpoint it used to call is gone: `stooq.com/q/d/l/?s=aapl.us&i=d` now returns a JavaScript **proof-of-work browser-verification challenge**, 796 bytes of SHA-256 grinding against a `/__verify` POST |
| FRED and Ken French need keys | both work with **no API key**. FRED gives a `datetime64[us]` index and one column named for the series id. Ken French returns `{0: frame, "DESCR": str}` with a **`period[D]` index** and values **in percent** |

Confirmed working, with real numbers: `AAPL` 2024-01-02 close `183.562195`;
`BTC-USD` 2024-01-02 close `44957.96875`; FRED `SP500` and yfinance `^GSPC`
agree to the cent on 2024-01-02 (`4742.83`); Ken French daily `Mkt-RF` for
2024-01-02 is `-0.70` **percent**, and its `DESCR` says the file was built from
the `202605` CRSP database.

### Stooq is out, and it is not a wiring problem

Reaching Stooq now requires solving a bot-detection challenge. **This plan will
not build that**, and it should not be built later either: an adapter whose job
includes defeating a site's browser verification is a maintenance liability
pointed at a moving target, and it is not a thing this project should ship.
Stooq is removed from the phase.

That costs the parent plan's stated reason for `DataQualityReport.source` —
"yfinance and Stooq disagree about splits". **The reason survives; the example
was the weak part.** The probe found a sharper one inside a single vendor: for
`AAPL` on 2020-08-25, days before the 4-for-1 split,

```
Close      124.824997      <- split-adjusted only
Adj Close  121.076965      <- split- and dividend-adjusted
```

a **3.1% gap on the same day from the same source**. A beta estimated on one is
not the beta estimated on the other, and nothing in a `ResultSet` distinguishes
them. So `label` must name the **adjustment policy**, not just the vendor
(decision 2), and the second-source cross-check becomes FRED — a genuinely
different pipeline for index levels and macro series, which Task 6.3 tests
against yfinance on `SP500`/`^GSPC`.

---

## Six decisions this plan settles

### 1. The adapter is sync in a thread, not a rewrite

`yfinance` is synchronous and reaches the network through `curl_cffi`; there is
no async entry point. `PriceSource.prices` is `async`. The adapter therefore
calls `asyncio.to_thread`, which is the whole of the impedance match. Doing
anything cleverer — reimplementing the Yahoo endpoint over `httpx` to stay
async-native, as `llm/providers/` does — would mean owning an undocumented,
changing wire format for no measured gain in a single-user local app whose
fetches are cached.

Consequence worth stating: `yf.download` writes progress and failure notices to
stdout/stderr through its own logger. The adapter silences it and converts the
empty-frame case into `DataUnavailableError`, because the Data Steward's
contract is that a named ticker either resolves or raises with the ticker named.

### 2. `label` names the adjustment policy, and the policy is a fixed choice

`label` is read into `DataQualityReport.source`, which is what a reader uses to
reproduce a number. Given the 3.1% gap above, `"yfinance"` is not enough
information to reproduce anything. The label is
`yfinance (Yahoo, split- and dividend-adjusted close)`.

And the policy is **not** a `DatasetSpec` field. Total-return prices are the
right input for every tool in the pricing family — a CAPM beta on price-return
data is measuring the wrong thing — so exposing the choice would only let a
Planner pick wrongly. One policy, named in the label, changeable in one place.

### 3. The cache is content-addressed parquet on disk, not a hypertable

The design puts price series in Timescale, and Task 6.8 builds that hypertable
for *datasets*. The fetch cache is a different object and stays separate:

- a cache entry's identity is `(source, symbol, window, adjustment, policy
  version)` and its only correctness criterion is "the same bytes come back";
- a dataset's identity is a user-facing id with a name, a retained blob and
  confirmed column roles;
- merging them makes cache eviction a data-loss risk, and `storage/` is
  gitignored and already the home of `keys.enc`, so a cache under it can be
  deleted without consequence.

`pyarrow` 25 is already a dependency. Cache misses on a *sub-window* of a
cached window are served from the cache, because a study of 2020–2023 and a
study of 2021–2022 should not be two fetches.

### 4. Factors are not prices, so they enter through their own frame

`Dataset.frame` is levels plus `_return`-suffixed returns, and a Ken French
factor is neither: `Mkt-RF` is already a return, and differencing it would be
wrong. `DatasetSpec` gains a `factors: list[str]` naming a factor set, and the
Data Steward joins the resolved factor columns onto `Dataset.frame`
**unsuffixed and undifferenced**, aligned on the same calendar. A test asserts
`to_returns` is never applied to a factor column — that is the one mistake in
this area that produces plausible, wrong numbers rather than a crash.

Percent-to-decimal conversion happens in the adapter, at the boundary, with a
test pinning `-0.70` → `-0.0070`. It is the single most common Ken French error
and it silently rescales every loading by 100.

### 5. Uploads are profiled deterministically; only the *role guess* is a model

Task 6.6 (dtype inference, date parsing, cardinality, missingness, candidate
role scoring) is arithmetic and gets no model — same reasoning as the Data
Steward and `charts/propose.py`. Task 6.7 asks a model only to *rank* candidate
mappings the profiler already found admissible, and **the user confirms before
ingest**, as §9 requires. A model may not invent a column, invent a role, or
map a column the profiler rejected as that role.

### 6. The `synthetic` label check is a substring match, so real labels must not contain it

`DataSteward.resolve` raises the `risk` flag when `"synthetic" in source.lower()`.
That is fine as long as no real adapter's label contains the word. A test in
Task 6.2 asserts it of every registered source, so the honesty seam cannot be
broken by a future label.

---

## Sequencing rationale

Data first, in dependency order (6.1 → 6.4), because everything above the
protocol is finished and the phase's other work is far more pleasant to build
and demonstrate against real numbers. The grounding fix (6.5) comes next
because real data means real runs, and a gate that withholds narrations over
their own citations makes every subsequent manual verification harder to read.
Uploads (6.6 → 6.8) follow as the second data intake path, sharing the
hypertable. Telemetry (6.9, 6.10) then makes the whole thing inspectable.
Integrations (6.11 → 6.13) are independent of each other and of everything
before them. PDF (6.14) is one task or none, depending on the decision below.
The sandbox (6.15) is last, as the design mandates, and gets its own
step-level treatment when reached — it is the only task in the project whose
tests are adversarial.

---

## Task 6.1: The yfinance `PriceSource`

**Files:** create `backend/src/econometrica/data/yahoo.py`; test
`backend/tests/data/test_yahoo.py`

One class, `YahooPriceSource`, implementing the protocol. `asyncio.to_thread`
around `yf.download(..., auto_adjust=False, progress=False, multi_level_index=False)`
— `auto_adjust=False` because we want `Adj Close` explicitly (decision 2) and
the default hides it. Flatten, name the series for the ticker, coerce to a
tz-naive `DatetimeIndex`, drop NaN rows.

**Tests must cover** (fake `yf.download` via monkeypatch for the unit tests):

- A normal fetch returns a `pd.Series` named for the ticker with a
  `DatetimeIndex`, and picks `Adj Close`, not `Close` — asserted with a frame
  where the two differ, so a swap cannot pass.
- An empty frame raises `DataUnavailableError` naming the ticker.
- A frame with a `MultiIndex` column header is handled, because
  `multi_level_index=False` is a request, not a guarantee across versions.
- A tz-aware index is converted to tz-naive; the Data Steward compares against
  `pd.Timestamp(spec.start)`, which is naive, and a naive-vs-aware comparison
  raises.
- `label` contains `yfinance` and the adjustment policy, and does **not**
  contain `synthetic`.
- `end <= start` raises before any network call.
- Nothing yfinance prints reaches stdout.

**Live tests** (`@pytest.mark.live`, skipping when Yahoo is unreachable):

- `AAPL` over a known window returns ≥ 5 rows of positive finite prices.
- The **2020-08-25 split case**: `Close` and `Adj Close` differ by ~3%, and the
  adapter returns the adjusted one. This is the test that makes decision 2
  mechanical rather than a comment.
- `BTC-USD` resolves — the Phase 4 gate question is about Bitcoin, and crypto
  has a 7-day calendar the equity path must not assume away.
- A junk ticker raises `DataUnavailableError`, proving the empty-frame belief
  against the real service and not only against the fake.

**Commit:** `feat(data): add yfinance price source`

**Landed.** 20 tests, 15 against a fake downloader and 5 live; the whole file
runs in 2.4s, so the live ones stay in the default suite rather than becoming a
thing nobody runs. All five real assertions hold against Yahoo: `AAPL` and
`BTC-USD` resolve, the window does not over-return, a junk ticker raises, and
**the split case comes back adjusted** — `121.08`, not the `124.82` raw close,
with a tolerance tight enough to exclude the wrong one by 3.7.

Three things the implementation had to decide that the plan had not:

- **`end` is exclusive**, confirmed by probe: asking for 2024-01-19 returns
  data to the 18th. The adapter adds a day. Getting this wrong would have
  shortened every window by one observation *and* changed every fingerprint,
  which no test above the adapter could have attributed to its real cause.
- **A missing `Adj Close` raises rather than falling back to `Close`.** A
  fallback would make `label` a lie, and the probe showed Yahoo serves the
  column for equities, indices, FX and crypto alike — so its absence is an
  anomaly, not a case to paper over.
- **yfinance is imported on first use, not at module scope.** It pulls in
  curl_cffi and a certificate bundle; every test that injects a downloader
  would otherwise pay for an import it never calls.

`_flatten` does not trust `multi_level_index=False`. The flag is honoured on
1.5.2, but the level's presence for a single ticker has moved between versions,
and a `ticker` level whose value is a differently-cased symbol still has to
flatten — so it tries the cross-section and drops the level if that fails.

---

## Task 6.2: Source registry, cache, and offline failures

**Files:** create `data/cache.py`, `data/registry.py`; modify `config.py`,
`api/deps.py`; tests `tests/data/test_cache.py`, `tests/data/test_registry.py`

`ECONOMETRICA_PRICE_SOURCE` becomes `Literal["none","synthetic","yahoo"]`.
`data/registry.py` maps the name to a factory, mirroring `llm/registry.py`'s
one-place-knows-every-provider shape, and `get_price_source` reads it.

`CachingPriceSource` wraps any source: parquet under
`storage_dir / "prices" / <source>/<symbol>/<hash>.parquet`, sub-window hits
served from a superset entry, and the wrapper's `label` is the wrapped
source's, so caching is invisible to the quality report.

**Tests must cover:**

- A second identical fetch does not call through — a counting fake proves it.
- A sub-window of a cached window is served from cache; a *superset* is not.
- A corrupt cache file is ignored and refetched rather than raising: a cache
  that can break the app is worse than no cache.
- Cache keys separate sources, so `yahoo:AAPL` and a future source's `AAPL`
  cannot collide.
- **Offline behaviour:** a wrapped source raising a transport error still
  serves a cached window, and raises `DataUnavailableError` with a message
  naming the offline source when there is nothing cached.
- `ECONOMETRICA_PRICE_SOURCE=yahoo` yields a caching Yahoo source from
  `get_price_source`; an unknown value fails at settings validation, not at
  the first run.
- **Decision 6:** every source the registry can build has a label that does
  not contain `synthetic`, except the synthetic one, which must.

**Commit:** `feat(data): add price source registry with an on-disk cache`

**Landed, and it needed a decision the plan had not anticipated.** 33 tests.
`ECONOMETRICA_PRICE_SOURCE=yahoo` now works end to end, and a live test drives
the real adapter through the Data Steward: two tickers over 2023 produce a
quality report with **no flags at all**, which is the assertion that proves the
end-exclusivity handling from 6.1 does not leave every real run carrying a
spurious `look_ahead` risk. A risk flag that cried wolf would be worse than
none.

### Cache entries expire, because an adjusted close is not a fixed number

The plan treated the cache as a pure fetch optimisation. It is not. **A vendor
recomputes adjusted closes every time a split or a dividend happens**, so the
series for a fixed window is not fixed over time — an entry served long enough
after it was written is a *different series*. A re-run that "reproduced" from
one would be reporting on the cache rather than on the data, which quietly
hollows out the claim the whole project rests on.

So entries carry a maximum age (one day by default: long enough that a run, its
re-run and its exports share one fetch). Past it they are refetched.

That leaves the case the plan's offline bullet was really about, and it resolves
the other way from what the bullet implied: when an entry is **stale and the
source is unreachable, it raises**. Serving it would be indistinguishable, to
everything above, from having fetched it — and there is no channel to say
otherwise, because `DataQualityReport.source` is read from `label` *before* the
fetch happens. The error names the staleness so the user can widen `max_age` if
that is the trade they want.

Offline-friendliness is therefore the cache being consulted *before* the source,
not the cache standing in for it: a fresh entry is served without a network
call at all, so being offline never arises for work already fetched.

Three smaller things:

- **`label` is now a read-only `@property` on the protocol.** It was declared
  `label: str`, which mypy reads as settable, so the cache wrapper's delegating
  property did not satisfy it. Nothing writes to it, so the protocol was simply
  over-specified — and a plain class attribute still satisfies a read-only
  member, which is how every other source declares it.
- **Cache paths carry a hash of the ticker**, because `^GSPC` and `_GSPC`
  sanitise identically and one serving the other's prices would be silent.
  Real symbols carry `^`, `=` and `.`, none of which may reach a Windows path
  unexamined.
- **The window recorded is the one requested, not the data's own span.** A
  request opening on a weekend gets data from the Monday; matching on that
  would make the request permanently uncacheable, as every repeat would look
  like it reached past what was stored.

`_UnconfiguredPriceSource` moved out of `api/deps.py` into
`data/unconfigured.py`, so the registry is the whole truth about what can be
built, and `deps.py` only reads the setting. A test asserts the settings
`Literal` and `registry.names()` agree — drift there would surface as a 500 on
a user's first run rather than at startup.

---

## Task 6.3: FRED, and the dead `risk_free` field

**Files:** create `data/fred.py`; modify `agents/data_steward.py`; tests
`tests/data/test_fred.py`, extend `tests/agents/test_data_steward.py`

`FredSeriesSource` over `pandas_datareader.data.DataReader(..., "fred")` in a
thread, same shape as 6.1. It resolves *series*, not prices — `DGS3MO` is a
rate in percent per annum, `SP500` is an index level — so the adapter carries
the same percent-to-decimal care as 6.4 and a `kind` on each known series.

Then close the `risk_free` gap: `DataSteward.resolve` resolves
`spec.risk_free` when set, converts an annualised rate to the frame's
frequency, and joins it as a column the factor and CAPM tools can bind to.
A rate is not a price and must not be differenced (decision 4).

**Tests must cover:**

- A FRED fetch returns a named series with a `DatetimeIndex` (the reader hands
  back `datetime64[us]` and an index named `DATE`).
- `DGS3MO`'s `5.46` becomes `0.0546` annualised, and the daily de-annualised
  value used in a frame is the compounding-consistent one, pinned by a test —
  `5.46/100/252` and `(1.0546)**(1/252)-1` differ in the fourth digit and both
  appear in published work; the plan picks the compounding form and says so.
- **`spec.risk_free` reaches the frame.** Currently it does not: a test that
  sets it and asserts a risk-free column exists must fail first, red, before
  the wiring is written.
- A risk-free column is never differenced.
- `risk_free` naming an unresolvable series raises with the series named.
- **Live:** FRED `SP500` and yfinance `^GSPC` agree within a tolerance on a
  fixed window — two independent pipelines for one series, which is what the
  attribution field is *for*.

**Commit:** `feat(data): add fred series source and wire the risk-free rate`

**Landed in three commits**, because a refactor had to come first.

**The de-annualisation question resolved to the compounding form**, and Ken
French settles it: its own file description defines `RF` as "the simple daily
rate that, over the number of trading days in the month, compounds to" the
monthly bill rate. So `(1+r)^(1/n)-1`, not `r/n`, and a study mixing our rate
with their factors stays internally consistent when 6.4 lands. A test pins the
value and asserts it differs from the naive form.

**The convention is a table, not a heuristic.** `DGS3MO` at `5.46` and a decimal
rate at `0.0546` are the same rate; `SP500` at `4742.83` is not a rate at all.
Inferring the scale from magnitude is precisely how a rate becomes an index
level, so an unlisted series raises and names the seventeen that are known.
Negative rates survive the conversion — policy rates have been below zero and a
clamp would misstate a decade of European work.

**`resolve_rate` takes an ordinary `PriceSource`.** A rate needed no protocol of
its own, only a convention, and taking the existing one means it inherits the
on-disk cache unchanged.

### The refactor the wiring forced: `data/base.py`

`PriceSource` and `DataUnavailableError` were defined in
`agents/data_steward.py`, so all five adapters imported *up* into `agents/` for
their own vocabulary. That was cosmetic until the steward needed to call *down*
into `data.rates.resolve_rate` — at which point it is a hard `ImportError`:
importing `data_steward` reaches the `data.rates` import before
`DataUnavailableError` is defined, and `data.rates` then asks a half-initialised
module for a name that is not there yet.

Both names moved to `data/base.py`, with `data_steward` re-exporting them, so
none of the sixteen existing import sites changed. Three guards in
`tests/data/test_layering.py`, each watched failing first:

- no module in `data/` may import from `agents/` — parsed with `ast`, because
  `data/base.py`'s own docstring discusses `agents/data_steward`;
- the re-export must be the *same object*, or an `except DataUnavailableError`
  would quietly stop catching what the adapters raise — and would keep passing
  in most tests, which import from one side only;
- **both import orders must succeed, in a subprocess.** In-process this proves
  nothing: by the time any test runs, both modules are already in `sys.modules`
  from collection. The same trap `tests/api/test_app_startup.py` documents for
  the tool registry.

The cycle guard was verified by recreating the exact cycle and watching it fail,
then confirming the same steward-imports-down change passes with `data/base.py`
in place — which is the check that proves the refactor *enabled* the feature
rather than merely tidying imports.

### Two smaller things

- **The rate is in the fingerprint.** A CAPM on excess returns and the same CAPM
  on raw returns are different analyses; a manifest that could not tell them
  apart would be claiming otherwise.
- **The rate source is not a setting.** FRED is the only source here that
  publishes a rate, needs no key, and cannot be substituted — Yahoo has no
  `DGS3MO` — so `get_rate_source` is unconditional and cached in its own
  directory. A synthetic-price run that asks for a real rate still carries its
  `synthetic_data` flag, and the report names the series.

**Verified on the composition, not just the adapters.**
`tests/data/test_live_integration.py` takes real Yahoo prices and a real FRED
yield through the steward into `capm`: AAPL against the S&P 500 over 2018–2023
monthly gives **beta 1.273** (se 0.139, t 9.17), alpha +1.57%/month, R² 0.55,
no quality flags, and a monthly risk-free peaking at 0.4543% — **5.59%
annualised**, which is where 3-month treasuries actually were in 2023. That last
number is the one that proves the conversion: skipping it would leave values
near 5.0 and every alpha catastrophically wrong.

It also caught a real mistake in its own first draft — a two-year window gives
23 aligned monthly observations once the undefined first return is dropped,
below `capm`'s `min_obs=30`. The window is six years for that reason.

---

## Task 6.4: Ken French factors

**Files:** create `data/famafrench.py`; modify `agents/schemas.py`,
`agents/data_steward.py`, `agents/planner.py`; tests
`tests/data/test_famafrench.py`, extend the steward and planner tests

`FamaFrenchFactorSource` over `pandas_datareader.famafrench.FamaFrenchReader`.
It must handle three things the probe exposed: the result is
`{0: frame, "DESCR": str}`; the index is `period[D]`, not a `DatetimeIndex`;
and **the values are percent**. It maps the library's `Mkt-RF`/`SMB`/`HML`
headers to the tools' `mkt_rf`/`smb`/`hml` parameter defaults, so a plan naming
`ff3` with default params works without the model knowing either spelling.

`DatasetSpec` gains `factors: str | None` naming a set (`ff3`, `ff5`,
`carhart4`), and the steward joins the columns per decision 4.

**Tests must cover:**

- `-0.70` becomes `-0.0070`. Pinned, standalone, with the comment explaining
  why it is the error worth its own test.
- `period[D]` becomes a `DatetimeIndex`, and a monthly dataset gets period-end
  labels consistent with `_RESAMPLE_RULE`.
- Column names arrive as the tools' parameter defaults.
- `RF` is available as a risk-free column, so a factor study needs no FRED call.
- A factor column is never passed through `to_returns` — the decision-4 test.
- **`ff3` runs end to end.** A `DatasetSpec` with `factors="ff3"` through the
  Econometrician produces a `ran` step with a beta and an alpha. This must fail
  first with the current `require_columns` error, which is the proof the gap
  was real.
- **Live:** the daily and monthly factor files both load and the daily
  `Mkt-RF` for 2024-01-02 is `-0.0070` after conversion.

**Commit:** `feat(data): add ken french factor source and unlock the factor models`

**Landed, and the three tools run.** The proof is
`tests/data/test_live_integration.py`, parametrised over all three against real
Ken French data — AAPL, 2018–2023 monthly:

| | mkt_rf | smb | hml | other | R² | alpha |
|---|---|---|---|---|---|---|
| `ff3` | +1.295 | −0.296 | −0.526 | | 0.621 | +1.29% (p .061) |
| `ff5` | +1.305 | −0.160 | −0.655 | rmw +0.300, cma +0.310 | 0.633 | +1.07% (p .130) |
| `carhart4` | +1.373 | −0.207 | −0.470 | mom +0.246 | 0.631 | +1.24% (p .071) |

Nothing above was asserted beyond "the market loading looks like a beta", and it
came out as theory predicts for a large-cap growth stock: negative size, negative
value, positive profitability, market loading agreeing with Task 6.3's CAPM beta
of 1.273. R² rises from CAPM's 0.549, and the alpha that was significant under
CAPM (p .034) is absorbed to insignificance under `ff5`. Signs and magnitudes
being *right* is worth more than any fixture could be.

### One probe finding that changed the design

**`F-F_Momentum_Factor_daily` cannot be read at all.** pandas-datareader 0.11.1
raises `TypeError` on it at every date range — `famafrench.py:118` compares a
string index against an int — so **`carhart4` is monthly-only**, and the set
declares that with the reason rather than failing inside a library the caller
never named. `ff3` and `ff5` are unaffected; their daily files parse fine.

### Four decisions the plan had not settled

- **`FactorSource` is its own protocol, not `PriceSource`.** A factor set is one
  object — `ff3` means three columns on a shared calendar plus the RF they are
  excess of — and splitting it into series to fit a price-shaped protocol would
  fetch the same file once per column.
- **`carhart4` takes SMB from the three-factor file, not the five-factor one.**
  They are constructed differently: 5.01 against 4.40 for the same month. Carhart
  is FF3 plus momentum, not FF5 minus two. A test pins it.
- **The monthly files' annual table is at key 1**, and reading it instead of key
  0 would give a handful of annual observations that look like a short sample
  rather than a wrong table.
- **A factor set supplies its own `risk_free`.** These factors are excess returns
  against Ken French's RF, so subtracting a FRED rate instead mixes two
  definitions in one regression. An explicit `risk_free` alongside `factors` is
  still honoured — the plan asked for it — but raises a `mixed_risk_free`
  warning.

Factors are reindexed onto the price calendar, never forward-filled: a factor
return belongs to its own period, and carrying one forward invents a second
period with the same return. Uncovered rows stay NaN for `align_series` to drop,
and a `factor_coverage` warning fires below 90% so the shortfall is in the
report rather than hidden in the tool's `nobs` — the library publishes on a US
trading calendar and lags the present by a month or two.

**The planner prompt had to change too**, or none of this would be reachable: it
now documents `factors` and `risk_free`, names the columns each produces, and
says outright that `ff3`/`ff5`/`carhart4` cannot run without a factor set.

---

## Task 6.5: The grounding gate's `(s3)` false positive

**Files:** modify `agents/grounding.py`; extend `tests/agents/test_grounding.py`

Carried from Phase 4. The gate reads the `3` in a `(s3)` step citation as a
claim about data and withholds the narration — over a citation format this
project's own prompts ask for. `_REFERENCE_WORDS` exempts `step 3` but not
`s3`.

**The fix is narrow and the tolerance does not move.** The gate is sound: it
caught a model writing `-15.066` where the statistic was `-15.065457`, and that
is the whole reason it exists.

**Tests must cover:**

- `(s3)` and `(s1, s3)` are exempt; the existing `step 3` exemption still is.
- `s3` where it is *not* a citation — bare, mid-sentence, not parenthesised —
  stays a claim, so the exemption is the citation form, not the letter.
- The `-15.066` case still fails. Non-negotiable, and it goes in the same test
  file next to the fix so nobody widens the tolerance to make a future case
  pass.
- A narration citing three steps and quoting three correct statistics
  publishes — the case that motivated the fix.

**Commit:** `fix(agents): exempt step citations from the numeric grounding gate`

**Landed, and the tolerance did not move.** The `-15.066` case still fails and
its test sits beside the exemption for exactly that reason.

**The exemption is keyed to the plan's actual step ids, not to the letter `s`.**
`check_grounding` takes `step_ids`, and a number is exempt only when the letters
immediately before it plus its digits spell an id the plan really contains. So
`(s3)` passes when s3 was planned and `(s7)` does not when it was not — the
guard against widening is a test that deliberately over-widens the rule and
watches two tests fail. It also means no assumption about how a Planner names
steps: whatever `PlanStep.id` holds is what is recognised. `step_ids` defaults to
empty, so the exemption is opt-in rather than a hole that opens by itself.

### The live probe found a second false positive of the same family

Running a real narration through `ministral-3:8b` — which is the only way this
kind of belief gets checked — the citation fix worked and the narration was
*still* withheld. The model had titled it **"Volatility Persistence in BTC-USD
Log Returns (2020–2024)"** and the gate flagged both years: `_is_year` exempts
"in 2008" by looking at the preceding *word*, and in a range that word is
whatever the title happened to say. The window is in the plan and is rendered
into the prompt, so restating it is what the model was asked to do.

`_is_year_range` now exempts `YYYY–YYYY`, requiring a plausible year on **both**
sides so a lone four-digit finding stays checked. The hyphen spelling needed its
own handling: `-2024` parses with a sign, so it arrives looking like a negative
number rather than a year. Seven dash characters are accepted, built from code
points rather than typed — all seven are indistinguishable on sight, and which
one appears is nobody's deliberate choice.

**Same prompt, same model, opposite outcome:** withheld before, published after,
14 numbers checked and no issues.

### One test that proved nothing until it was rewritten

The first narrator-level regression test cited `(s1)` and passed *without* the
fix. A GARCH beta near 0.90 rounds to `1` at zero decimals, so the citation's
digit was grounded by coincidence — which is also why the original bug looked
intermittent rather than deterministic. The test now cites `s7`, and it was
verified failing without the fix and passing with it.

---

## Task 6.6: Upload profiling and schema inference

**Files:** create `services/ingest.py`; test `tests/services/test_ingest.py`

CSV, XLSX and Parquet in, a `FileProfile` out: per column a dtype, a parsed-date
verdict, cardinality, missingness, min/max, a sample, and **scored candidate
roles** (`date`, `ticker`, `price`, `return`, `volume`, `factor`, `ignore`).
Deterministic (decision 5). `pandas`, `openpyxl` and `pyarrow` are already
dependencies; nothing new is needed.

**Tests must cover:** wide and long layouts; a date column that is text; a
European decimal comma; a return column detected by being centred near zero
and bounded, versus a price column that is positive and trending; an ambiguous
column offered as two candidates rather than guessed; an empty file, a
one-column file and a file whose header is on row 3 all failing with a message
naming the problem; and an oversized file refused before it is read into memory.

**Commit:** `feat(services): add upload profiling with candidate column roles`

**Landed, 36 tests.** `profile_upload` describes a file and scores every role
each column *could* play; it never picks one. That separation is what leaves
Task 6.7's model a real job with no room to invent — it may reorder candidates
this module found admissible and nothing else.

### The delimiter sniffer was the real hazard

`pandas.read_csv(sep=None)` delegates to `csv.Sniffer`, which chooses from the
whole alphabet when there is no delimiter to find. On a file whose only column
is `price` **it split on the `r`**, returning columns `p` and `ice` — a file
that read successfully and wrongly, which is far worse than one that refuses.
Found by a test expecting the single-column refusal and not getting it.

The delimiter now comes from a closed set (`,` `;` tab `|`) with comma as the
fallback, and that turned out to settle a second question too. `1,200` is `1.2`
under a decimal comma and `1200` under a thousands comma, and **the string alone
cannot say which** — but a file using commas for decimals cannot also use them
to separate fields, which is exactly why such exports are semicolon-separated.
So a comma-delimited file means thousands and any other delimiter admits
decimals. Both spellings have a test.

`encoding="utf-8-sig"` because Excel writes a BOM on nearly every CSV it
exports, and without it the first column is named `﻿date` and matches no
hint.

### Verified against real files, not only constructed ones

A genuine `yfinance` CSV export: `Date` → `date`, and `Adj Close`, `Close`,
`High`, `Low`, `Open` all → `price` at 1.00 from their name hints, with `Volume`
correctly ambiguous but correctly *ordered* — `volume` 1.00 ahead of `price`
0.60. A real Ken French `ff5` export: all five factors → `factor` 1.00 with
`return` 0.85 offered second, which is honest because a factor is shaped like a
return. A long-format panel: `symbol` → `ticker`, layout `long`.

A test also round-trips **this project's own export shape** through the
profiler, since a user's likeliest upload is a file they exported from here:
`AAPL`, `AAPL_return` and `risk_free` all have to land correctly, and they do.
It uses the synthetic source, so it needs no network.

Refusals carry the reason: empty, header-not-on-row-one (detected from
`Unnamed: N` columns, the commonest broken export), one column, no rows,
unknown extension, corrupt parquet, and oversized — the last checked against
the file's size on disk **before it is opened**, so a hostile upload cannot
exhaust memory on its way to being rejected.

---

## Task 6.7: Column-role mapping the user confirms

**Files:** create `agents/column_mapper.py`, `api/routers/uploads.py`,
`frontend/src/components/uploads/*`; tests alongside

The model ranks the profiler's admissible mappings and explains its choice;
the API returns the proposal, the user edits and confirms, and **only the
confirmed mapping is ingested**. A model may not invent a column, invent a
role, or assign a role the profiler scored as inadmissible — a test proves each
refusal.

**Tests must cover:** a proposal validating against the profile; a proposal
naming a column that does not exist being rejected and retried with the error
(the `agents/base.py` loop); ingest refused without a confirmation token;
the frontend rendering the proposal with every column editable and the
confirm button disabled until a date column and at least one value column are
mapped; and a profile with an unambiguous mapping needing **no model call**,
so the common case is free.

**Commit:** `feat(agents): add confirmed column-role mapping for uploads`

**Landed in three commits** — the mapping core, the API, and the screen.

**Confirmation is a gate, not a formality.** `confirm_mapping` is the only thing
that produces a `ColumnMapping` with `confirmed` set, and `apply_mapping`
refuses anything else, so a model's suggestion cannot be acted on by
construction rather than by convention.

**The user and the model are constrained differently, deliberately.** A user may
map a column to a role the profiler never suggested — only the person who
exported the file knows a column of small positive numbers is penny prices
rather than returns. A model may not: it chooses among admissible candidates,
and naming a missing column, assigning a ruled-out role, or inventing a role are
each rejected and retried with the problem named.

**The model is skipped when there is nothing to decide**, and falls back to the
profiler when it will not comply. An upload must not fail because a model
declined, and the user confirms either way.

`apply_mapping` emits long-format observations — `ts`, `symbol`, `field`,
`value` — whatever the file's layout, which is the shape 6.8's hypertable
stores. A wide file names its symbol in the header; a long one has a ticker
column and names the *field* there instead.

**One gap closed on the way:** `FileProfile` now records its delimiter, because
ingest happens in a later request and a semicolon export re-read with commas
comes back as one column. CSVs are re-read with `dtype=str` so a decimal comma
survives to `apply_mapping`, which knows from the profile how to parse it.

**Verified against a live model.** On a real Yahoo export only `Volume` was
ambiguous, so only `Volume` was put to `ministral-3:8b`; it answered `volume` in
one attempt and its reason replaced the profiler's with something written for a
reader — *"Values are whole numbers typical of trading volumes, not prices"*.

**The screen is rendered in `/gallery.html`, not mounted in the app.** Confirming
a mapping stores the blob and the mapping but nothing analysable yet — the
observations hypertable and `UploadedPriceSource` are 6.8 — so a user-facing
upload button would promise something the backend cannot honour. It goes in the
dev harness for now, which is where Phase 5's chart types were looked at, and
6.8 mounts it once an uploaded file can actually be run.

Two notes on verification. The browser pane would not composite frames in this
session, so the screen was checked through the DOM and computed styles — both
themes, the disabled state, spacing, overflow — rather than looked at. That is
weaker than Phase 5's standard and worth redoing when a screenshot is possible.
And the first draft hand-rolled a button and used arbitrary Tailwind values
(`text-[var(--text-primary)]`) where the codebase has a `Button` with a
`primary` variant and `text-text-primary` utilities; it now uses both.

---

## Task 6.8: Dataset store — hypertable plus retained blob

**Files:** create `db/models/dataset.py`, an Alembic revision, and
`data/uploaded.py`; tests `tests/db/test_dataset_model.py`,
`tests/data/test_uploaded.py`

`datasets` (id, project, name, source label, column roles, blob path,
fingerprint) and `observations` as a **Timescale hypertable** on `(dataset_id,
ts, symbol)` — the first hypertable in the project, so the migration is
hand-checked, and `alembic check` sees neither hypertable conversion nor a
CHECK added to an existing table (CLAUDE.md's database note). The original file
is retained under `storage_dir`, per §9.

`UploadedPriceSource` then exposes an ingested dataset through the *same*
`PriceSource` protocol, so an uploaded series and a fetched one are
interchangeable above the seam, and its label names the file and its ingest
time.

**Tests must cover:** the hypertable exists and is chunked (queried from
Timescale's catalogue, against the real database); round-tripping a long-format
dataset; a duplicate `(dataset, ts, symbol)` rejected; deleting a dataset
cascading its observations and leaving the blob; the blob's bytes matching what
was uploaded; and every constraint reaching a migration, extending
`tests/db/test_migrations.py`.

**Commit:** `feat(db): add dataset store with a timescale observations hypertable`

**Landed.** 34 tests, and the whole upload path now ends somewhere: a real
two-ticker Yahoo export profiles with no ambiguity (so no model call), stores
**1506 observations**, resolves through the Data Steward as 36 monthly rows with
no quality flags, and runs `capm` — beta 0.812, t 5.78 — on data that came from
a file rather than a fetch.

### The hypertable needed three things autogenerate could not give it

- **The conversion itself is invisible to alembic.** A hypertable and an
  ordinary table are the same table to autogenerate, so a database built from
  the migrations alone would have got a plain one — which behaves identically
  until the row counts get interesting. `op.execute("SELECT
  create_hypertable(...)")` is hand-written, and the test asserts it against
  Timescale's own catalogue rather than against the DDL we wrote.
- **`create_hypertable` creates its own index**, `observations_ts_idx`, which
  nothing in the models declares — so `alembic check` found an index it had not
  been told about and wanted to drop it, on **every** run. Fixed by passing
  `create_default_indexes => FALSE` and declaring `ix_observations_ts`
  ourselves, which keeps the schema fully described by the metadata. That is
  the only condition under which `alembic check` means anything here.
- **`field` had to join the key.** The plan said (dataset, ts, symbol); a wide
  file mapping both a close and a volume produces two rows for the same date and
  symbol, so that key would have refused an ordinary file. Timescale also
  requires the partitioning column in any unique constraint, which `ts` already
  satisfied.

The hypertable test asserts `projects` is *not* listed alongside it, so the
catalogue query is visibly discriminating rather than one that would pass
against any table.

### The rest

`UploadedPriceSource` serves an ingested dataset through the same `PriceSource`
protocol as Yahoo — which is the entire point: nothing above the protocol had to
learn that uploads exist. Its label names the file and the ingest date, and a
test asserts it never contains `synthetic`, since the Data Steward's risk flag
fires on that substring and a user's own data reported as generated would be as
wrong as the reverse. Levels are preferred but a returns-only file still
resolves.

**Re-confirming replaces rather than duplicates.** A user who realises they
mapped a column wrongly confirms again; leaving the first ingest in place would
double every observation and the second mapping would never take effect. The
blob is untouched either way, which is what makes the correction cheap.

`GET /api/projects/{id}/datasets` lists confirmed uploads only. An unconfirmed
one has a blob and a proposal but no observations, and listing it would offer a
user data they never agreed to.

**Still to mount:** the `ColumnMapping` screen remains in `/gallery.html`. The
backend can now honour an upload end to end, so the only thing left is deciding
where "Data" belongs in the three-pane layout — a UI question that deserves its
own increment rather than being tacked onto a migration.

---

## Task 6.9: Telemetry — spans to Postgres, OTLP, metrics

**Files:** create `telemetry/__init__.py`, `telemetry/spans.py`,
`telemetry/exporter.py`, `api/routers/metrics.py`; modify `main.py`; tests
under `tests/telemetry/`

`opentelemetry-sdk` 1.44 is already installed and unused. A custom
`SpanExporter` writes to Postgres; OTLP export is optional and off unless
`OTEL_EXPORTER_OTLP_ENDPOINT` is set. The metrics the design names: latency
p50/p95/p99, token spend by provider and role, tool error rates, validator
rejection rate, plan revision counts, and database timings.

`services/tracing.py` already records the agent DAG with tokens, cost and
latency per step. **Telemetry must not duplicate it.** The run DAG is the
domain record and stays; spans cover what it does not — HTTP handlers, database
timings, tool execution, provider calls at transport level.

**Tests must cover:** a span reaching Postgres with its trace and parent ids
intact; nesting preserved across an `await`; the exporter's failure never
breaking the request it was measuring (a test injects one); no OTLP attempt
when the endpoint is unset; percentiles computed against a known distribution;
and no token or cost double-counting between spans and `run_steps`.

**Commit:** `feat(telemetry): add opentelemetry spans persisted to postgres`

---

## Task 6.10: Trace viewer and cost dashboard

**Files:** `frontend/src/components/telemetry/*`, extend
`api/routers/runs.py`; tests alongside

The design's Trace artifact: the run DAG rendered in the canvas so any turn can
be opened to see which model decided what. Plus a cost and latency dashboard
over the 6.9 metrics.

The canvas already has a Trace tab from Task 5.4 showing steps as a list. This
replaces it with the DAG, and the dashboard is a new canvas tab.

**Tests must cover:** a DAG rendering parent links as edges, not just order; a
step expanding to its prompt and response; provider and model per step; refused
and failed steps visually distinct from ran ones; cost totals matching the
sum of the steps; and — per Phase 5's lesson — **screenshot it in both themes
and look at it** before calling it done.

**Commit:** `feat(frontend): add run trace viewer and cost dashboard`

---

## Task 6.11: MCP client with an allowlist

**Files:** create `mcp/client.py`, `mcp/allowlist.py`; modify
`db/models/project.py`; tests under `tests/mcp/`

The `mcp` package is **not** currently a dependency and will need adding.
Discovered tools pass an explicit per-project allowlist before any agent may
call them; MCP is off by default and inherits/overrides per chat through the
existing `resolve_capabilities`.

**Tests must cover:** the parent plan's named criterion — **an unlisted tool
cannot be invoked**, asserted against a server offering one; discovery listing
tools without exposing them; a server that disappears mid-session failing the
step rather than the run; allowlist changes taking effect without a restart;
and every MCP tool call appearing in the trace with its arguments, because a
tool call nobody can audit is worse than no tool call.

**Commit:** `feat(mcp): add mcp client with a per-project tool allowlist`

---

## Task 6.12: Project-scoped retrieval over pgvector

**Files:** create `services/rag.py`, `db/models/document.py`, a migration;
tests under `tests/services/`

PDF and text chunked into pgvector, retrieval **scoped to the project** — the
scoping is the security property and gets its own test. Embeddings come from a
provider already in `llm/registry.py`; no new vendor.

**Tests must cover:** chunking with overlap preserving sentence boundaries;
a query never returning another project's chunks; retrieval attributed in the
trace; an unembeddable document failing at upload with a reason; and — the
invariant this project cannot break — **retrieved text never becoming a
number**. A narration citing a statistic that came from a document rather than
a `ResultSet` must still be blocked by the grounding gate.

**Commit:** `feat(services): add project-scoped document retrieval`

---

## Task 6.13: Web search, off by default, attributed

**Files:** create `tools/web_search.py`; tests alongside

Provider-agnostic behind one interface, off by default, every result
attributed in the trace with its URL and retrieval time.

**Tests must cover:** disabled by default at project and chat level; a search
result reaching the trace with its source; **search text never grounding a
number** (same gate as 6.12); and a search provider being down degrading the
run rather than failing it.

**Commit:** `feat(tools): add attributed web search behind a capability toggle`

---

## Task 6.14: PDF export — a print stylesheet, no new dependency

**Files:** `frontend/src/styles/print.css`, modify the canvas and export menu;
tests alongside

**Decided 2026-07-27: the zero-dependency route.** The browser's own pipeline
produces the report PDF, and the charts on the page print with it. Neither
stack gains a dependency, and §10's PDF line closes.

The two routes not taken, recorded so they are not relitigated. `jspdf` +
`svg2pdf.js` over `Plotly.toImage(..., "svg")` would give a true per-chart
**Download PDF** button for two npm packages — the upgrade path if one is ever
wanted. **kaleido is ruled out**, not deferred: the backend holds no Plotly
JSON, so it would mean reimplementing all fourteen TypeScript renderers in
Python in order to export a chart nobody had looked at.

**Tests must cover:** a print stylesheet that lays out one chart per page
without clipping; the manifest present in the printed output, because an
exported artifact that cannot be traced back is what this project exists not to
produce; canvas chrome, tab strips and buttons suppressed; and both themes
printing legibly on white — a dark-theme chart printed dark is ten pages of
toner. Screenshot the print preview and look at it, per Phase 5's lesson.

**Commit:** `feat(exports): add pdf output for reports and charts`

---

## Task 6.15: Sandboxed code escape hatch

**Files:** create `sandbox/runner.py`, `agents/quant_coder.py`; tests under
`tests/sandbox/`

Built last, as §2 and the parent plan both require. Subprocess isolation, no
network, no filesystem, an import allowlist, and CPU/memory/wall-clock caps.
Off by default, project-scoped only (`resolve_capabilities` already refuses to
let a chat override it), Validator sign-off mandatory, and outputs marked in
the UI as an unvalidated method.

**Every restriction gets an escape attempt as its test** — the parent plan is
explicit and it is the right standard. Windows is the awkward part: `resource`
limits are POSIX-only, so caps come from Job Objects or a watchdog, and this
task gets its own short design note before implementation rather than
discovering that mid-task.

**Commit:** `feat(sandbox): add isolated subprocess runner for generated code`

---

## Task 6.16: Phase 6 e2e — full regression

**Files:** `frontend/e2e/platform.spec.ts`, and fixes to
`frontend/e2e/analysis.spec.ts`

A regression across all six phases on **real data**: create a project, upload a
CSV and confirm its column mapping, run an analysis on a real ticker, read the
charts, open the trace DAG, check the cost dashboard, export the archive, and
re-run from the manifest.

This is also where the flaky Phase 4 assertion gets fixed.
`analysis.spec.ts:227` asserts an unpublished narration always carries
grounding issues, but when the Validator refuses there is nothing to narrate
and no issues to report — a third path the spec does not model. It passes or
fails on the model's mood, which makes the whole gate unreadable. The fix is to
assert the *reason* a narration was withheld is one of the three real reasons,
and annotate which happened, following `canvas.spec.ts`'s
chart-or-no-chart precedent.

**Commit:** `test(e2e): close phase 6 with a full-stack regression`

---

## Phase 6 definition of done

- `uv run pytest`, `npx vitest run`, `npx tsc --noEmit`, `npm run test:e2e`
  green; `ruff` and `mypy src` clean; `alembic check` reports no drift.
- A run on a **real ticker** produces charts, publishes a narration, and
  re-runs from its manifest.
- `ff3` runs end to end on real Ken French factors.
- An uploaded CSV is profiled, mapped with the user's confirmation, and
  analysed through the same protocol as a fetched ticker.
- The trace viewer renders a run's DAG and the dashboard totals match the steps.
- An MCP tool outside the allowlist cannot be invoked, proven by a test.
- Every sandbox restriction has an escape attempt that fails.
- `ECONOMETRICA_PRICE_SOURCE=synthetic` **still works and still carries its
  `synthetic_data` risk flag** — real adapters are an addition, never a
  replacement, because the synthetic source is what makes the pipeline
  runnable with no network at all.

---

## Decisions taken 2026-07-27

Both were the two open dependency questions this phase had to settle before the
tasks they gate. Recorded here so the reasoning survives the session.

1. **PDF export is a print stylesheet** (Task 6.14). No new dependency in
   either stack. kaleido is ruled out rather than deferred, for the reason
   given in that task.
2. **Stooq is dropped; FRED is the independent cross-check.** Stooq's endpoint
   is behind a browser-verification challenge, and an adapter that defeats one
   is not something this project should ship. A keyed vendor — Tiingo, Alpha
   Vantage — was considered and rejected: it breaks the design's "free, no API
   keys required to start" and puts a signup between a clone and a first run.
   FRED needs no key, is a genuinely separate pipeline, and already agrees with
   yfinance to the cent on `SP500`/`^GSPC`, which is what makes it a usable
   cross-check rather than a second guess.
