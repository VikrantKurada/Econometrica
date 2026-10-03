# What we reversed

- **The point:** eight things we got wrong, designed and did not build, or had
  changed underneath us. The tempting fix was almost always the wrong one.
- **Read time:** about 9 minutes
- **Do first:** read [R5](#r5). It is the clearest case of the tempting fix
  and the right fix side by side.

A decision log with no reversals in it is a decision log nobody is being
honest in.

| # | Reversal | The lesson |
|---|---|---|
| R1 | Stooq, dropped | An adapter that must defeat a bot check is a liability |
| R2 | kaleido, ruled out | Render where the rendering logic lives |
| R3 | `shadowed_symbol`, not built | A diagnostic that costs a network call is not free |
| R4 | `read_csv(sep=None)`, abandoned | Know the delimiter. Do not sniff it |
| R5 | The e2e gate was model-dependent | A flaky test is often a modelling gap |
| R6 | Four of five market data assumptions were wrong | Verify against the real service |
| R7 | Asserting constraint names was not enough | Assert the values too |
| R8 | Built and tested but not reachable | Look for a module imported only by its own tests |

---

<a id="r1"></a>

## R1. Stooq, dropped from the project

### What we planned

**Stooq was to be the independent cross-check**, the second opinion on whether
a price series was right.

The original design named four data sources: yfinance, Stooq, FRED and the
Ken French library.

### What we found

**Two separate problems. Either would have been enough.**

1. **`pandas-datareader` resolved to 0.11.1**, which implements exactly six
   sources: `bankofcanada`, `econdb`, `eurostat`, `famafrench`, `fred`,
   `oecd`. `DataReader(..., "stooq")` raises `NotImplementedError`. This is
   not a wiring problem.
2. **The CSV endpoint it used to call now answers with a JavaScript
   proof-of-work browser-verification challenge.**

### The decision

**Dropped, not worked around.**

> An adapter whose job includes defeating a bot check is not an adapter, it is
> a liability.

Such an adapter would:

- break silently
- break at the worst moment
- mean tracking someone else's anti-automation measures forever

### What replaced it

**FRED.**

- No API key.
- A genuinely separate pipeline.
- It agreed with yfinance **to the cent** on `SP500` against `^GSPC`.

That is a better cross-check than Stooq would have been. The independence is
real, not nominal.

---

<a id="r2"></a>

## R2. kaleido, ruled out rather than deferred

### What we planned

PDF export of charts, server-side, using kaleido to rasterise Plotly figures.

### Why it does not work here

**The backend holds no Plotly JSON.**

- The fourteen renderers are TypeScript.
- They are where the chart actually gets decided: which trace type, which
  palette slot, which axis treatment, which annotations.
- Server-side rendering would mean reimplementing all fourteen in Python.
- The result would be an export of a picture **nobody had looked at**. It
  would differ from the one on screen in ways nobody would notice until it
  mattered.

### The decision

**Ruled out, not deferred.** Deferring implies it is a matter of time. It is
a matter of where the rendering logic lives, and it lives in the right place
already.

| Output | Comes from |
|---|---|
| PDF | The browser's own print pipeline, driven by `styles/print.css`. No new dependency in either stack |
| PNG, SVG | The live Plotly graph. The image **is** the one you looked at |

---

<a id="r3"></a>

## R3. `shadowed_symbol`, designed and deliberately not built

### The idea

**Warn the user when an upload shadows a symbol the market source also
carries.**

- When a run draws on both an uploaded file and a market source, the upload
  wins for any symbol it carries.
- If the market source **also** carries that symbol, the user is making a
  choice they might not realise they are making.
- We designed a `shadowed_symbol` warning for exactly this.

### Why it was not built

**Knowing the market source also carries the symbol requires fetching it.**

- That is a counterfactual fetch whose only product is a warning.
- Worse, it would make an upload-only run require the network it was meant to
  avoid.
- Someone working offline with their own data would find the application
  reached for Yahoo anyway, to tell them something they already knew.

### What we have instead

**`mixed_sources`**, an **info**-severity flag naming every ticker under the
source that served it.

- It is the authoritative record of what was actually used.
- It costs nothing.
- It needs no extra fetch.

### The general lesson

A diagnostic that costs a network call is not free. "It would be nice to warn
about X" is not sufficient reason to make a system reach outside itself.

---

<a id="r4"></a>

## R4. `read_csv(sep=None)`, abandoned

### What we did

**We let pandas sniff the delimiter of an uploaded file.** It seemed obviously
right: users upload files with tabs, semicolons, pipes, and you cannot make
them tell you which.

### What happened

**`sep=None` delegates to `csv.Sniffer`, which picks from the whole
alphabet.**

On a one-column file holding the header `price`, it split on the `r` and
returned two columns: `p` and `ice`.

### The fix

**The delimiter comes from a closed set now.**

### The bonus

**It also settled the comma question.** `1,200` is ambiguous in isolation: one
thousand two hundred, or 1.2?

A file using commas for decimals **cannot also use them as separators**. So:

| Delimiter | A comma inside a number means |
|---|---|
| Comma | Thousands |
| Anything else | Decimals are admitted |

The ambiguity dissolves once you know the delimiter. That is another argument
for knowing it, not sniffing it.

---

<a id="r5"></a>

## R5. The e2e gate was model-dependent

### The problem

**The gate passed on some models and failed on others, so a red build told
you nothing about whether the code was broken.**

`analysis.spec.ts` asserted that when a narration is withheld, the canvas
explains it as ungrounded.

### The wrong fix, which we nearly made

Loosen the assertion. Accept either message. Move on.

### The right fix

**Model the third path.**

A narration is withheld for two reasons, and the spec asserted only one.

The other one went like this:

1. `check` rejects an invented citation or an unparseable reply **before**
   `check_grounding` runs.
2. So in that case the grounding report is empty.
3. The canvas told users their model "cited numbers no result supports".
4. The model had in fact returned prose where JSON was asked for.

Two different failures, one message, and one of them was a lie.

What changed:

- `Narration.withheld_reason` is now a closed set: `""`, `ungrounded`,
  `unusable_draft`.
- The spec asserts the reason and annotates which happened.
- The canvas tells the truth.

### The lesson

> A flaky test is often a modelling gap wearing a costume.

When a test passes on some inputs and fails on others, ask whether the system
has two behaviours you had collapsed into one. Ask that before you loosen the
assertion.

---

<a id="r6"></a>

## R6. Four of five assumptions about market data were wrong

**The original design's version floors were two years stale by the time we
got to phase 6.** Everything below was verified against the real services.

### yfinance

| We assumed | It is |
|---|---|
| Version 0.2.x | **1.5.2** |
| `Adj Close` is present | `auto_adjust=True` is the default and **removes** it |
| An unknown ticker raises | It returns an empty `(0, 6)` frame and logs |
| Columns are flat for one ticker | They are a `MultiIndex` even then |
| `end` is inclusive | It is **exclusive**, so passing the requested end through loses the last trading day of every window |

### The one that moves numbers

**Not the vendor. The adjustment policy.**

| AAPL, 2020-08-25 | Close |
|---|---|
| Split-adjusted | `124.82` |
| Dividend-adjusted | `121.08` |

Same day, same source, **3.1% apart**. Nothing in a `ResultSet` distinguishes
them.

So `PriceSource.label` names its policy, and `DataQualityReport.source`
carries it. Reproducing a number means knowing which of two equally real
series produced it.

### Ken French

| We assumed | It is |
|---|---|
| Values are decimals | Values are **percent**. `Mkt-RF` of `-0.70` means -0.70%. Forgetting the conversion rescales every loading by 100 |
| The index is a datetime | The index is a `period[D]` |

Both are silently wrong if missed. So `data/famafrench.py` converts at the
boundary, and both conversions have their own test.

### Ollama

**Capabilities come from `/api/show`, not `/api/tags`.** Tags reports neither
context length nor tool support.

Guessing from the model name was wrong in both directions:

- On this machine, 6 of 13 chat models cannot call tools.
- Real context windows run from 512 to 262144, not the 8192 the adapter used
  to claim for everything.

**The context key is architecture-prefixed.** Read `general.architecture` to
name it. Matching `*.context_length` alone also catches
`mistral3.rope.scaling.original_context_length`, which is a **smaller** number
and would silently truncate prompts.

---

<a id="r7"></a>

## R7. Asserting constraint names was not enough

### What we had

`tests/db/test_migrations.py` asserted that every CHECK constraint in the
models reached some migration. It passed.

### What broke

**The constraint's name never changed. Its contents did.**

1. `ck_run_steps_agent_known` has been in the initial revision since phase 4.
2. `quant_coder` was added to `STEP_AGENTS` in Python.
3. The test stayed green.
4. **A fresh database rejected every sandbox step.**

The developer machine's database already had the column and the old
constraint. The old constraint happened to be permissive enough for
everything being tested at the time.

### The fix

**The test now asserts every value of each vocabulary reaches a migration
too**, not only every constraint name.

### The compounding factor

**The test database is built by `Base.metadata.create_all`, not from the
migrations.** So a constraint test passing against Postgres says nothing
about whether a revision exists for it.

Two independent blind spots happened to line up. That is usually how this
kind of bug survives.

---

<a id="r8"></a>

## R8. Things that were built and tested but not reachable

**A shape of gap worth naming, because we found it three times.**

`UploadedPriceSource`:

- satisfied the `PriceSource` protocol from phase 6
- was fully tested
- was **never constructed by anything**

The claim "uploads are servable through the same protocol as Yahoo" was true
of the class and false of the application.

The same shape turned up in:

| Module | Wired on |
|---|---|
| `data/uploaded.py` | 2026-07-30, by `data/project_source.py` |
| `tools/web_search.py` | 2026-07-30 |
| `services/rag.py` | 2026-07-31 |
| `mcp/` | 2026-08-01 |

### How to find it

**Look for a module imported only by its own tests.**

Nothing in this tree is now in that state.

It is the check to run after any phase that builds capability ahead of the
feature that uses it. That is most phases.

---

## The pattern

**Six of these eight reversals share a shape:**

```mermaid
flowchart LR
    A["A reasonable assumption"] --> B["A test that agreed with it"]
    B --> C["Reality, which did not"]
    C --> D{"the tempting fix"}
    D -->|"loosen the test"| E["the gap survives"]
    D -->|"model what is actually there"| F["the gap closes"]

    style E fill:#f8d7da,stroke:#e34948,color:#14181d
    style F fill:#d4edda,stroke:#1baf7a,color:#14181d
```

**The tempting fix is almost always available and almost always wrong.**

When a test disagrees with reality, one of them is describing a system that
does not exist. Find out which before changing either.

**Next, 2 minutes:** open [The roadmap](../5-roadmap/) and read the rule that
governs every item on it.

---

[Documentation](../README.md) · [4. Decisions](README.md) ·
[The central decision](the-central-decision.md) ·
[Trust mechanisms](trust-mechanisms.md) ·
[Platform choices](platform-choices.md) · **Reversals**
