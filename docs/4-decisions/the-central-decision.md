# The central decision

- **The point:** the model selects a tool, the tool computes. A gated,
  clearly marked code escape hatch covers what the registry does not.
- **Read time:** about 7 minutes
- **Do first:** read [the probe](#the-probe) under D3. Five runs, one wrong
  answer, and it settled the design.

Everything else in this project is downstream of one question, asked on
2026-07-24 and answered the same day.

> **How does a language model produce econometrics you would stake a decision
> on?**

| # | Decision | In one line |
|---|---|---|
| D1 | LLMs never compute statistics | They select from a registry. The tools compute |
| D2 | Registry first, with a gated code escape hatch | Versatile and still trustworthy |
| D3 | The escape hatch is off by default, and its results are marked | A sandbox result must never look like a registry result |

---

<a id="d1"></a>

## D1. LLMs never compute statistics

### Context

**A language model can sit in front of an econometrics problem in exactly two
positions:**

1. as the thing that computes
2. as the thing that decides what to compute

Almost everyone puts it in the first position, because that is what the demo
looks like.

### Decision

**They select from a registry of typed, versioned econometric tools. The tools
compute.**

### What it costs

| You lose | How much it matters |
|---|---|
| The ability to answer questions the registry does not cover | A real cost. It is why [D2](#d2) exists |
| The illusion of unlimited capability | A marketing cost, not an engineering one. Worth paying |

### What it buys

**Everything else in this documentation.** Once numbers can only come from
tested functions:

```mermaid
flowchart TB
    D1["LLMs never compute statistics"]
    D1 --> A["A number needs an identity<br/>and a provenance"]
    D1 --> B["A model's output must be<br/>checkable, so it must be a schema"]
    D1 --> C["Library objects must not leak,<br/>or the boundary is fiction"]
    A --> A1["<code>ResultSet</code> with a <code>Manifest</code>"]
    A --> A2["Reproducibility, and re-run"]
    B --> B1["<code>AnalysisPlan</code>, validated<br/>at construction"]
    B --> B2["The grounding gate has<br/>something to check against"]
    C --> C1["<code>econ.types</code> as a seam"]
    C --> C2["37 tools cost nothing<br/>above <code>econ/</code>"]
```

None of those is a feature anyone added. They are consequences.

### Consequences that keep coming up

- statsmodels, arch and linearmodels result objects never leave a tool module.
- Vendor SDK types never leave a provider adapter.
- Nothing under `tools/` may become a source of numbers. `tools/` is context.
  `econ/` is computation.

---

<a id="d2"></a>

## D2. Registry first, with a gated code escape hatch

### Context

**A registry is bounded and the questions are not.** Sooner or later someone
asks for something none of the 37 tools does.

### Options

**Three were on the table. The first two are the ones most products pick.**

| Option | What it is | Verdict |
|---|---|---|
| **A: tool registry only** | The model never writes code. Deterministic, fully traceable, bounded by whatever the registry contains | *Rejected: too narrow.* A workbench that cannot answer a question outside its list is a workbench people stop opening |
| **B: code generation in a sandbox** | The model writes statsmodels and arch code and it runs. Unbounded | *Rejected: wrong default* for a tool whose entire value is trustworthy numbers |
| **C: registry first, with a gated escape hatch** | The registry serves the canonical majority. The escape hatch covers the rest | **Chosen** |

What option B brings with it:

- hallucinated methodology
- non-reproducible results
- a real security surface
- nothing that can be unit-tested ahead of time

How option C handles a question with no fitting tool:

1. A Quant Coder writes code.
2. The code runs in a locked-down subprocess.
3. The Validator must sign off.
4. The result is marked as using an unvalidated method.

### Decision

**C. It is the only one satisfying both "highly versatile" and "numbers worth
staking a decision on."**

It is also the most security-sensitive component in the system. That is why
it was **built last**, once everything else was stable.

### Evidence

The probe that settled it is described under [D3](#d3). It is also what
turned the escape hatch from a feature into a feature-with-a-marking.

---

<a id="d3"></a>

## D3. The escape hatch is off by default, and its results are marked

### Context

**We built the sandbox and then asked the obvious question: is a sandboxed
result trustworthy?**

### The probe

`ministral-3:8b`, temperature 0, asked for a Gini coefficient. Five runs.

Four produced correct code. The fifth produced code that:

- imported only numpy, which was allowed
- touched only the frame it was given
- ran in milliseconds, well inside every cap
- satisfied the output contract exactly
- reported a Gini coefficient of **-42.49**

A Gini coefficient is bounded in [0, 1].

### What that means

**Every restriction held. The answer was still wrong.**

- A sandbox is a security control.
- It tells you code did not escape.
- It cannot tell you code was right. No sandbox can, because correctness is
  not a property of execution.

### Decision

**Three gates. Each refuses. None degrades.**

```mermaid
flowchart LR
    Q["No registry tool fits"] --> G1{"the project<br/>enables it?<br/><i>a chat cannot</i>"}
    G1 -->|no| R1(["refused"])
    G1 -->|yes| G2{"the tier has<br/>a Validator?<br/><i>single is refused outright</i>"}
    G2 -->|no| R2(["refused"])
    G2 -->|yes| G3{"a Quant Coder<br/>is configured?"}
    G3 -->|no| R3(["refused"])
    G3 -->|yes| RUN["it runs, sandboxed"]
    RUN --> MARK["and it is marked"]

    style R1 fill:#f8d7da,stroke:#e34948,color:#14181d
    style R2 fill:#f8d7da,stroke:#e34948,color:#14181d
    style R3 fill:#f8d7da,stroke:#e34948,color:#14181d
```

**The marking is the deliverable.** A sandbox result must never look like a
registry result:

| Where | What it says |
|---|---|
| `ResultSet.tool` | `sandbox:<method>`. A colon cannot appear in a registry tool name, so nothing in `econ/` can collide with it by accident |
| `Manifest.tool_version` | The literal string `unvalidated`. Not a number, deliberately: there is nothing to compare a number against |
| The run banner | Alerts exactly as `synthetic_data` does |
| The print-only provenance block | Says it in words |

All four are derived from the result itself. **A marker that travels
separately from the thing it marks is a marker that can be lost.**

### The test that proves it, and the test that does not exist

| The live test | |
|---|---|
| Asserts | The code **runs and is marked** |
| Never asserts | The arithmetic is right |

Asserting the arithmetic would claim a property this feature does not have.
It would also fail one run in five.

### One more consequence

**The Planner is told `code_steps` exists only when the capability is on.**

- `AnalysisPlan.code_steps` is default-empty.
- Told about it always, the Planner reaches for the escape hatch on any hard
  question.
- Every such plan is then refused *after* the model call has already been paid
  for.

A capability a model cannot use is a capability it should not be able to see.

---

## The three layers of the sandbox, and why only two are security controls

**"We sandboxed it" flattens three layers into one. Only two of the three are
security controls.**

| Layer | Enforced by | Strength |
|---|---|---|
| Import allowlist | A gated `__import__` in the generated code's own builtins | **Bypassable**, via `().__class__.__base__.__subclasses__()` |
| Forbidden operations | A PEP 578 audit hook | **This is what stops the operations.** It fires from C and cannot be unregistered |
| Resource caps | A Job Object on Windows, `setrlimit` on POSIX | The OS. The process with its caps is **the real boundary** |

**`SMUGGLE` in `tests/sandbox/test_escapes.py` defeats the allowlist
deliberately.** Every test under it proves the audit hook still holds after
the weak layer has fallen.

That is the correct way to test defence in depth:

1. Assume the outer layer is gone.
2. Check the inner one still bites.

Neutering the hook fails 12 of 28 escape tests. That is how the tests were
shown to be doing anything at all.

**Two facts explain why the allowlist has to be where it is:**

1. **The `import` audit event cannot enforce an allowlist.** It is raised by
   `_find_and_load`, which never runs on a `sys.modules` cache hit. So
   `import socket`, after pandas has loaded it, fires nothing.
2. **Blocking `open` outright breaks `arch`**, which imports
   `pyarrow.pandas_compat` at *fit* time. So writes are denied, and reads are
   permitted only under `sys.prefix` and `sys.base_prefix`. Both exclude
   `storage/`.

---

## What would change our minds

| Decision | What would change it |
|---|---|
| **D1** | Nothing available today. A model shown to compute a GARCH persistence correctly 100% of the time would still leave you unable to reproduce it, because the model would not remember how |
| **D2** | The registry growing to cover essentially every question people actually ask. The escape hatch would then be dead weight and worth removing. A good problem, and we are not close to it |
| **D3** | Nothing. The marking costs almost nothing, and the alternative is a number that looks tested and is not |

**Next, 2 minutes:** open [Trust mechanisms](trust-mechanisms.md) and read D4,
the grounding gate.

---

[Documentation](../README.md) · [4. Decisions](README.md) ·
**The central decision** · [Trust mechanisms](trust-mechanisms.md) ·
[Platform choices](platform-choices.md) · [Reversals](reversals.md)
