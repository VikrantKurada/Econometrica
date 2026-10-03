<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/doc-art-of-the-possible-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="../assets/doc-art-of-the-possible-light.svg">
  <img alt="Five rungs of capability, each one made reachable by the one below it" src="../assets/doc-art-of-the-possible-light.svg">
</picture>

# 6. The art of the possible

- **The point:** a number you can check is the hard rung. Every rung above it
  is engineering.
- **Read time:** about 8 minutes
- **Do first:** look at [the ladder](#the-ladder). Five rungs, bottom to top,
  and the label on each arrow is the reason the next rung is reachable.

This is the speculative section. Everything before it describes what is, or
what is planned. This page is about what becomes **reachable** if the thing
holds.

"The art of the possible" is usually where documentation stops being honest.
So this page holds itself to a rule:

> Every claim has to name what makes it possible, and the answer has to be
> something that already exists.

---

## The ladder

**The hard rung is the first one.**

```mermaid
flowchart BT
    R1["<b>1. A number you can check</b><br/>a single result with a manifest under it"]
    R2["<b>2. A study that survives its author</b><br/>re-runnable a year later, told per step what moved"]
    R3["<b>3. A desk with a memory</b><br/>every question ever asked, with its evidence"]
    R4["<b>4. Analysis that watches itself</b><br/>recomputed on a schedule, alerting when it moves"]
    R5["<b>5. A market for method</b><br/>tools published, versioned and cited by strangers"]

    R1 -->|"because the manifest names the fingerprint,<br/>the tool version and the source"| R2
    R2 -->|"because a run is a row, not a conversation"| R3
    R3 -->|"because re-run needs no model in the loop"| R4
    R4 -->|"because a tool is a typed function<br/>with a version"| R5

    style R1 fill:#d4edda,stroke:#1baf7a,color:#14181d
```

- Everything above rung one is engineering.
- Rung one is a design decision, and it is the one you cannot retrofit.

| You can | You cannot |
|---|---|
| Add scheduling to a system that produces trustworthy numbers | Add trustworthy numbers to a system that schedules untrustworthy ones |

That is the whole argument for building in this order. It is why the
capability that looks least impressive in a demo is the one that matters.

---

## Six things that become possible

| # | What | Made possible by | Roadmap |
|---|---|---|---|
| 1 | Analysis you can send instead of describe | The manifest | L1 |
| 2 | Knowing what your organisation has already tried | Refusals stored as results | L3 |
| 3 | Efficiency as a monitored quantity | Ten efficiency tools, and a re-run with no model | L4 |
| 4 | Method that circulates the way papers do | A tool is a typed, versioned function | L2 |
| 5 | Teaching by refusal | A refusal carries a `because` | Not on the roadmap |
| 6 | A defensible record | Everything recorded by default | L6 |

### 1. Analysis you can send instead of describe

**Today an analyst finishes a piece of work and what leaves the building is a
slide.** The evidence stays behind, in a notebook, on a laptop, in a form
nobody else can execute.

**What makes it possible:** the manifest. A file carrying the data
fingerprint, the tool identity and version, the parameters and the results is
small. It is checkable in two modes:

| Mode | How |
|---|---|
| With the data | Re-execute |
| Without the data | Verify internal consistency and a signature |

**What changes:** the unit of exchange between analysts stops being a
narrative and starts being an artifact. That is a bigger shift than it
sounds. Narratives are unfalsifiable. Artifacts are not.

**Roadmap:** [L1](../5-roadmap/long-term.md#l1-a-portable-verifiable-result-format).

---

### 2. Knowing what your organisation has already tried

**Every organisation's most valuable knowledge is what it tried and
abandoned. That is exactly the knowledge that never gets written down.**
Nobody documents a failure. They quietly stop.

**What makes it possible:** refusals are first-class results here. Each of
these is a persisted step with a reason attached:

- a GARCH declined for lack of ARCH effects
- a run blocked because the narration could not be grounded
- a window that had too few observations

**What changes:** "has anyone looked at this?" gets an answer that includes
the dead ends.

| Without it | With it |
|---|---|
| A new analyst spends a week rediscovering the dead end | They find out in thirty seconds that the desk tried a cointegration approach in March and the Johansen test refused |

**Roadmap:** [L3](../5-roadmap/long-term.md#l3-institutional-memory).

---

### 3. Efficiency as a monitored quantity, not a paper

**Market efficiency studies are published as snapshots:** this market, this
window, this battery of tests, this conclusion. They are famously hard to
replicate, because the handling choices that drive the result are rarely
recorded.

**What makes it possible:**

- ten efficiency tools in the registry
- a composite score
- a re-run that consults no model
- a manifest under every point

**What changes:** an efficiency score becomes a time series, not a finding.
You can:

1. Watch a market's weak-form efficiency move.
2. Shade the regimes with a model that is already in the registry.
3. Hold the data fixed and vary the handling, to find out how much of the
   movement is real.

The third capability is rare. Most efficiency research cannot do it, because
the handling was never pinned down.

**Roadmap:** [L4](../5-roadmap/long-term.md#l4-continuous-market-efficiency-monitoring).

---

### 4. Method that circulates the way papers do

**How a new econometric method reaches practitioners today:**

1. It appears in a paper.
2. Someone implements it in a notebook.
3. The notebook gets shared.
4. Three people copy it with small differences.

A year later nobody can say which version produced which result.

**What makes it possible:** a tool is a typed function with a version and a
contract test suite, and a manifest already names both.

**What changes:** `tool_version: "2.1.0"` in a manifest becomes a citation.

| Before | After |
|---|---|
| "We used a GARCH" | "We used *this* GARCH, and here is the exact function" |

The difference matters more in econometrics than in most fields. The
implementation choices move the answer: which optimiser, which starting
values, which variance targeting.

**Roadmap:** [L2](../5-roadmap/long-term.md#l2-a-published-tool-registry),
after [M4](../5-roadmap/medium-term.md#m4-an-extensible-tool-registry).

---

### 5. Teaching by refusal

**This one is not on the roadmap. It is the one I would most like someone to
build.**

**What makes it possible:** a gate refusal carries a `because`, and the
`because` is written for a person.

> `garch` requires ARCH effects. The ARCH-LM test gives a statistic of 4.21
> with a p-value of 0.52, so the null of no ARCH effects is not rejected.
> Fitting a GARCH here would return a persistence figure with nothing behind
> it.

**What changes:** a learner asks for something wrong and gets told exactly
why it is wrong, on their own data, with the statistic in front of them.

That is a better econometrics tutorial than most textbooks, for two reasons:

- it is responsive
- the example is theirs

It works because it is not a tutorial mode bolted on. It is the same refusal
a professional gets. Nobody had to write a curriculum.

---

### 6. A defensible record, without anyone having to be diligent

**What makes it possible:** everything is recorded by default.

- the trace and the prompts
- the rejected attempts and the token counts
- the data fingerprint and the adjustment policy
- the refusals
- the withheld narrations

None of it requires the analyst to remember to record anything. That
matters, because the analyst will not.

**What changes:** the gap between "what a regulator would want" and "what
exists" narrows to three things: identity, immutability and retention. Those
are ordinary engineering, not a cultural change.

**Roadmap:** [L6](../5-roadmap/long-term.md#l6-regulatory-grade-audit-export),
with the honest caveat attached: this needs a compliance-literate reviewer
before anyone claims it.

---

## What this generalises to

**The pattern here is not about econometrics.**

```mermaid
flowchart LR
    A["A domain where<br/>the methods are settled"] --> C
    B["and the failure is<br/>expensive and invisible"] --> C
    C["<b>Let the model select,<br/>not compute</b>"]
    C --> D["Every output traces<br/>to a tested function"]
    D --> E["Reproducibility becomes<br/>achievable rather than<br/>aspirational"]
    E --> F["And then everything on<br/>the ladder is reachable"]
```

**Three conditions make a domain a candidate:**

1. **The methods are settled.** Nobody needs a model to invent a new way to
   estimate a CAPM beta. The reference implementations are correct and have
   been for years. What people need is help choosing among them.
2. **The failure is expensive and invisible.** A wrong number does not throw
   an exception. It gets put in a memo and someone acts on it.
3. **Correctness is checkable given the inputs.** This is what makes
   reproducibility possible in principle. It rules out domains where the
   output is a judgement.

| Domains that fit | Domains that do not |
|---|---|
| Clinical trial analysis | Anything where the answer is an opinion |
| Actuarial work | Anything where the methods are still being invented |
| Structural engineering calculations | Anything where nobody would notice a wrong answer |
| Environmental modelling | |

**Also fits:** anywhere with a body of validated methods, a regulator, and a
habit of putting numbers in documents.

**In the domains that do not fit, a model that computes is fine.** There was
never a reproducibility claim to break.

---

## The thing I would most want to be true

**Not a feature. A change in what people expect.**

Today:

1. You show someone a number produced by an AI system.
2. The reasonable response is to check it.
3. Checking it is expensive.

That expense limits how much of this work can be delegated. It limits it much
more than model capability does.

If a number arrived with its evidence attached, cheaply, verifiably, by
default, the expensive step goes away. Not because the model got better.
Because you stopped having to trust it.

> **That is the actual bet.** Not that AI will do econometrics. That AI will
> select econometrics, and that the selection is the part worth having.

---

## Read next

**You have reached the end of the documentation.**

**Next, 2 minutes:** go back to the [repository README](../../README.md) and
check the four rows of the Prerequisites table under Quickstart against your
machine.

Or go back to something concrete:

- **[What is in the box](../2-product/)**: what exists today.
- **[The central decision](../4-decisions/the-central-decision.md)**: the
  choice this all rests on.
- **[`CLAUDE.md`](../../CLAUDE.md)**: the working notes. More useful than any
  of this if you are about to change something.

---

[Documentation](../README.md) · [1. Why](../1-why/) · [2. Product](../2-product/) ·
[3. Architecture](../3-architecture/) · [4. Decisions](../4-decisions/) ·
[5. Roadmap](../5-roadmap/) · **6. The art of the possible**
