"""Generates the hero illustration at the top of each documentation section.

Six drawings, each written twice so the pages can serve a light and a dark file
through a `<picture>` element and let GitHub pick by the reader's own theme.
Everything about how they are drawn lives in ``diagram_kit``; this file is the
content.

Mermaid does most of the diagramming in these pages, because GitHub renders it
natively and a diagram that lives in the Markdown next to the prose it explains
is a diagram that gets updated when the prose does. These six are the ones
Mermaid cannot draw: they are layouts and comparisons rather than graphs, and a
flowchart engine given a layout produces a graph of boxes that happens to look
like one.

Regenerate after editing:

    uv run python docs/assets/build_doc_diagrams.py
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from diagram_kit import (
    MONO,
    Theme,
    arrow_down,
    arrow_right,
    band_heading,
    columns,
    elbow,
    line,
    open_svg,
    paragraph,
    rect,
    text_el,
    wrap,
    write_pair,
)

W = 1280
MARGIN = 44


def header(t: Theme, title: str, standfirst: str) -> list[str]:
    return [
        text_el(MARGIN, 54, title, fill=t.text, size=27, weight="700"),
        text_el(MARGIN, 80, standfirst, fill=t.muted, size=14),
    ]


def footer(t: Theme, y: float, note: str) -> list[str]:
    return [
        line(MARGIN, y - 20, W - MARGIN, y - 20, stroke=t.border, width=1),
        text_el(MARGIN, y, note, fill=t.faint, size=12.5),
    ]


# --- 1. why: the trust gap ----------------------------------------------------


@dataclass(frozen=True)
class Failure:
    mode: str
    detail: str
    mechanism: str
    evidence: str
    proof: str


FAILURES = (
    Failure(
        mode="It invents the statistic",
        detail=(
            "Asked for a beta, a model will produce a plausible number without"
            " ever touching the data."
        ),
        mechanism="Models select, tools compute",
        evidence=(
            "A plan names a tool from a registry of 37 typed, versioned"
            " functions. The function does the arithmetic."
        ),
        proof="econ/registry.py",
    ),
    Failure(
        mode="It runs a method the data cannot support",
        detail=(
            "A GARCH fitted to a series with no ARCH effects returns a"
            " persistence figure a reader will take seriously."
        ),
        mechanism="Preconditions that refuse",
        evidence=(
            "A gate is checked against the real series before the tool runs."
            " The refusal names the reason, so it teaches something."
        ),
        proof="econ/gates.py",
    ),
    Failure(
        mode="It writes a number the results do not contain",
        detail=(
            "The methodology can be perfect and the paragraph describing it"
            " still carries a figure from nowhere."
        ),
        mechanism="The numeric grounding gate",
        evidence=(
            "Every number in the prose is matched against what the tools"
            " computed. One that does not match withholds the whole narration."
        ),
        proof="agents/grounding.py",
    ),
)


def build_trust_gap(t: Theme) -> str:
    height = 608
    p = open_svg(
        W, height, t,
        title="The trust gap, and the three mechanisms that close it",
        label=(
            "Three ways a language model produces a wrong number, each paired with"
            " the mechanism in Econometrica that prevents it"
        ),
    )
    p += header(
        t,
        "Why this exists",
        "Three ways a language model produces a number you should not trust, and what closes each.",
    )

    left_w = 430
    right_x = MARGIN + left_w + 96
    right_w = W - MARGIN - right_x
    row_h = 128
    top = 118

    p += band_heading(t, MARGIN, top - 14, "the failure", t.series[7])
    p += band_heading(t, right_x, top - 14, "the mechanism", t.series[2])

    for index, failure in enumerate(FAILURES):
        y = top + index * (row_h + 16)

        p.append(rect(MARGIN, y, left_w, row_h, fill=t.card, stroke=t.border))
        p.append(rect(MARGIN, y, 3, row_h, fill=t.series[7], r=1.5))
        p.append(text_el(MARGIN + 16, y + 27, failure.mode, fill=t.text, size=15, weight="600"))
        p += paragraph(t, MARGIN + 16, y + 51, failure.detail, limit=54)

        p.append(arrow_right(MARGIN + left_w + 28, y + row_h / 2, 40, stroke=t.faint, width=1.6))

        p.append(rect(right_x, y, right_w, row_h, fill=t.card, stroke=t.border))
        p.append(rect(right_x, y, 3, row_h, fill=t.series[2], r=1.5))
        p.append(
            text_el(right_x + 16, y + 27, failure.mechanism, fill=t.text, size=15, weight="600")
        )
        p.append(
            text_el(
                right_x + right_w - 16, y + 27, failure.proof, fill=t.faint, size=11.5,
                family=MONO, anchor="end",
            )
        )
        p += paragraph(t, right_x + 16, y + 51, failure.evidence, limit=76)

    p += footer(
        t, height - 26,
        "None of the three is a prompt asking the model to behave. Each is code with a test that"
        " fails when it stops working.",
    )
    p.append("</svg>")
    return "\n".join(p) + "\n"


# --- 2. product: the workbench ------------------------------------------------


def build_workbench(t: Theme) -> str:
    height = 570
    p = open_svg(
        W, height, t,
        title="The Econometrica workbench",
        label="The three-pane workbench layout and what each pane holds",
    )
    p += header(
        t,
        "What is in the box",
        "One window, three panes, and a run that leaves a trail behind it.",
    )

    top = 124
    shell_h = 212
    p += band_heading(t, MARGIN, top - 16, "the window", t.series[0])

    panes = (
        (
            "Projects",
            t.series[0],
            220,
            [
                "A tree of projects and chats.",
                "A project owns the data, the",
                "documents, the MCP servers,",
                "the model assignments and",
                "the capability toggles.",
            ],
        ),
        (
            "Canvas",
            t.series[2],
            0,
            [
                "Where an analysis is run and where the answer lands. A tab per chart,",
                "plus Narrative, Diagnostics, a Trace DAG and a Cost dashboard. Risk",
                "flags sit above the tabs where no tab can hide them.",
                "",
                "Select a project with no chat open and this pane shows its Data instead:",
                "upload a file, confirm the column mapping, see what is stored.",
            ],
        ),
        (
            "Chat",
            t.series[6],
            340,
            [
                "A streaming conversation",
                "with one model. It calls no",
                "tools and produces no charts,",
                "which is deliberate: a chat",
                "and a run are different acts.",
            ],
        ),
    )

    fixed = sum(width for _, _, width, _ in panes)
    flexible = W - 2 * MARGIN - fixed - 2 * 14
    x = MARGIN
    for title, accent, width, lines in panes:
        pane_w = flexible if width == 0 else width
        p.append(rect(x, top, pane_w, shell_h, fill=t.card, stroke=t.border))
        p.append(rect(x, top, pane_w, 30, fill=t.bg, r=10))
        p.append(rect(x, top + 20, pane_w, 10, fill=t.bg, r=0))
        p.append(line(x, top + 30, x + pane_w, top + 30, stroke=t.border, width=1))
        p.append(rect(x, top, 3, 30, fill=accent, r=1.5))
        p.append(text_el(x + 16, top + 20, title, fill=t.text, size=14, weight="600"))
        for row, prose in enumerate(lines):
            p.append(text_el(x + 16, top + 58 + row * 18, prose, fill=t.muted, size=12.5))
        x += pane_w + 14

    p.append(
        text_el(
            MARGIN, top + shell_h + 26,
            "All three panes resize and collapse. Collapsing the last one standing is refused,"
            " because that leaves a window with no way back.",
            fill=t.faint, size=12,
        )
    )

    flow_y = top + shell_h + 76
    p += band_heading(t, MARGIN, flow_y, "what a run leaves behind", t.series[1])

    artifacts = (
        ("Charts", "one per result shape,\nlight and dark, each\nwith a table view"),
        ("Narrative", "prose that survived\nthe grounding gate,\nor an honest absence"),
        ("Diagnostics", "the deterministic\nchecks, tri-state:\npassed, failed, unjudged"),
        ("Trace", "a DAG of every model\ncall and tool run,\nrejected attempts too"),
        ("Cost", "tokens and spend by\nprovider and by role,\nlatency from spans"),
        ("Exports", "JSON, Markdown, CSV,\nXLSX, ZIP, PNG, SVG,\nPDF via print"),
    )
    card_w, xs = columns(W, MARGIN, len(artifacts), gap=12)
    for (title, body), cx in zip(artifacts, xs, strict=True):
        p.append(rect(cx, flow_y + 18, card_w, 92, fill=t.card, stroke=t.border, r=8))
        p.append(rect(cx, flow_y + 18, 3, 92, fill=t.series[1], r=1.5))
        p.append(text_el(cx + 13, flow_y + 40, title, fill=t.text, size=13.5, weight="600"))
        for row, prose in enumerate(body.split("\n")):
            p.append(text_el(cx + 13, flow_y + 58 + row * 15, prose, fill=t.muted, size=11))

    p += footer(
        t, height - 24,
        "Every result carries the manifest that reproduces it: the data fingerprint and the tool"
        " version. A data export adds the source the prices came from.",
    )
    p.append("</svg>")
    return "\n".join(p) + "\n"


# --- 3. architecture: layers and seams ----------------------------------------


@dataclass(frozen=True)
class Layer:
    name: str
    modules: str
    note: str
    accent_index: int


LAYERS = (
    Layer(
        "Browser",
        "React 19 · TypeScript · Vite · TanStack Query · Zustand · Plotly",
        "Renders. Never decides what a number is.",
        0,
    ),
    Layer(
        "Transport",
        "REST for state · SSE for anything that streams",
        "Two shapes, chosen per endpoint by whether the answer arrives at once.",
        0,
    ),
    Layer(
        "API routers",
        "projects · chats · messages · runs · uploads · documents · mcp · exports · metrics",
        "The composition root. The only layer that knows a project has settings.",
        6,
    ),
    Layer(
        "Services",
        "capabilities · datasets · ingest · mapping · documents · rag"
        " · exports · keystore · tracing",
        "Everything that needs the database and is not a route.",
        6,
    ),
    Layer(
        "Agents",
        "planner · steward · econometrician · validator · narrator · visualizer"
        " · coder · writer · researcher · mapper",
        "Knows nothing about projects, chats or the database.",
        4,
    ),
    Layer(
        "Core",
        "econ/ registry, gates, diagnostics · sandbox/ · charts/ · llm/ · data/ · tools/ · mcp/",
        "Pure computation and typed adapters. No web, no ORM.",
        2,
    ),
    Layer(
        "Stores",
        "Postgres 16 + TimescaleDB + pgvector · on-disk price cache"
        " · encrypted keystore · blob storage",
        "One engine doing three jobs: hypertables, JSONB, vectors.",
        3,
    ),
)

SEAMS = (
    (
        "econ.types.ResultSet",
        "No statsmodels, arch or linearmodels object leaves a tool module.",
    ),
    ("llm.types", "No vendor SDK type leaves a provider adapter."),
    (
        "data.PriceSource",
        "Yahoo, FRED, an upload and the generator are the same protocol.",
    ),
    ("tools.Retriever", "Retrieval is a protocol so agents/ stays off db.models."),
    (
        "mcp.Connector",
        "The transport is behind an interface the research loop cannot see.",
    ),
)


def build_layers(t: Theme) -> str:
    height = 690
    p = open_svg(
        W, height, t,
        title="Econometrica architecture: layers and the seams between them",
        label="The layered architecture, with the five typed seams nothing is allowed to cross",
    )
    p += header(
        t,
        "The architect's view",
        "Seven layers. Five seams. The seams are the part worth arguing about.",
    )

    left_w = 830
    top = 122
    row_h = 62
    gap = 8

    p += band_heading(t, MARGIN, top - 14, "layers", t.series[0])
    for index, layer in enumerate(LAYERS):
        y = top + index * (row_h + gap)
        accent = t.series[layer.accent_index]
        p.append(rect(MARGIN, y, left_w, row_h, fill=t.card, stroke=t.border, r=8))
        p.append(rect(MARGIN, y, 3, row_h, fill=accent, r=1.5))
        p.append(text_el(MARGIN + 16, y + 22, layer.name, fill=t.text, size=14.5, weight="600"))
        p.append(
            text_el(MARGIN + 122, y + 22, layer.modules, fill=t.faint, size=10.5, family=MONO)
        )
        p.append(text_el(MARGIN + 16, y + 44, layer.note, fill=t.muted, size=12))
        if index < len(LAYERS) - 1:
            p.append(arrow_down(MARGIN + left_w / 2, y + row_h + 1, gap - 2,
                                stroke=t.border, width=1.2))

    seam_x = MARGIN + left_w + 34
    seam_w = W - MARGIN - seam_x
    p += band_heading(t, seam_x, top - 14, "seams nothing crosses", t.series[2])
    for index, (name, note) in enumerate(SEAMS):
        y = top + index * 96
        p.append(rect(seam_x, y, seam_w, 84, fill=t.card, stroke=t.border, r=8))
        p.append(rect(seam_x, y, 3, 84, fill=t.series[2], r=1.5))
        p.append(
            text_el(seam_x + 14, y + 24, name, fill=t.text, size=13, weight="600", family=MONO)
        )
        for row, prose in enumerate(wrap(note, 42)):
            p.append(text_el(seam_x + 14, y + 46 + row * 16, prose, fill=t.muted, size=11.5))

    p += footer(
        t, height - 26,
        "A seam is not a folder. It is a type that a test asserts nothing above it depends"
        " on, which is why swapping Yahoo for an uploaded file changed no code above data/.",
    )
    p.append("</svg>")
    return "\n".join(p) + "\n"


# --- 4. decisions: the central one --------------------------------------------


@dataclass(frozen=True)
class Option:
    label: str
    name: str
    body: str
    verdict: str
    chosen: bool


OPTIONS = (
    Option(
        "A", "Tool registry only",
        "The model never writes code. It picks from a fixed set of typed, versioned functions."
        " Deterministic, traceable, and bounded by whatever the registry happens to contain.",
        "Rejected: too narrow", False,
    ),
    Option(
        "B", "Code generation in a sandbox",
        "The model writes statsmodels and arch code and the code runs. Unbounded, and it brings"
        " invented methodology, results nobody can reproduce, and a real security surface.",
        "Rejected: wrong default", False,
    ),
    Option(
        "C", "Registry first, with a gated escape hatch",
        "The registry answers the canonical majority. When nothing fits, a Quant Coder writes code"
        " that runs in a locked-down process, and the result is marked unvalidated in the manifest,"
        " the banner and the printout.",
        "Chosen", True,
    ),
)

GATES = (
    (
        "The project enables it",
        "A chat cannot. It is the most security-sensitive toggle in the system.",
    ),
    ("The tier has a Validator", "The single tier is refused outright rather than degraded."),
    ("A Quant Coder is configured", "No role assigned means no code path, not a silent fallback."),
    ("The marking survives", "sandbox:<method> in the tool name, unvalidated in the manifest."),
)


def build_central_decision(t: Theme) -> str:
    height = 600
    p = open_svg(
        W, height, t,
        title="The central decision: how generative AI produces econometrics",
        label="Three options for how an LLM produces econometrics, and why the third was chosen",
    )
    p += header(
        t,
        "The decision everything else follows from",
        "How does a language model produce econometrics you would stake a decision on?",
    )

    top = 122
    card_w, xs = columns(W, MARGIN, 3, gap=20)
    card_h = 214

    for option, x in zip(OPTIONS, xs, strict=True):
        accent = t.series[2] if option.chosen else t.faint
        p.append(
            rect(x, top, card_w, card_h, fill=t.card, stroke=accent if option.chosen else t.border)
        )
        p.append(rect(x, top, 3, card_h, fill=accent, r=1.5))
        p.append(text_el(x + 18, top + 34, option.label, fill=accent, size=24, weight="700"))
        p.append(text_el(x + 48, top + 34, option.name, fill=t.text, size=15, weight="600"))
        for row, prose in enumerate(wrap(option.body, 50)):
            p.append(text_el(x + 18, top + 62 + row * 17, prose, fill=t.muted, size=12.5))
        p.append(
            text_el(
                x + 18, top + card_h - 18, option.verdict,
                fill=accent if option.chosen else t.faint, size=12.5, weight="700",
            )
        )

    chosen_x = xs[2]
    p.append(
        elbow(
            [
                (chosen_x + card_w / 2, top + card_h),
                (chosen_x + card_w / 2, top + card_h + 22),
                (MARGIN + (W - 2 * MARGIN) / 2, top + card_h + 22),
                (MARGIN + (W - 2 * MARGIN) / 2, top + card_h + 44),
            ],
            stroke=t.series[2], width=1.6,
        )
    )
    p.append(
        arrow_down(
            MARGIN + (W - 2 * MARGIN) / 2, top + card_h + 36, 10, stroke=t.series[2], width=1.6
        )
    )

    gate_y = top + card_h + 78
    p += band_heading(t, MARGIN, gate_y, "and the escape hatch is gated three ways, then marked", t.series[2])
    gate_w, gxs = columns(W, MARGIN, 4, gap=14)
    for (title, note), gx in zip(GATES, gxs, strict=True):
        p.append(rect(gx, gate_y + 18, gate_w, 92, fill=t.card, stroke=t.border, r=8))
        p.append(rect(gx, gate_y + 18, 3, 92, fill=t.series[2], r=1.5))
        p.append(text_el(gx + 14, gate_y + 42, title, fill=t.text, size=13, weight="600"))
        for row, prose in enumerate(wrap(note, 36)):
            p.append(text_el(gx + 14, gate_y + 62 + row * 15, prose, fill=t.muted, size=11.5))

    p += footer(
        t, height - 24,
        "A live probe settled it: a local model asked for a Gini coefficient wrote correct"
        " code four runs in five, and the fifth ran cleanly and reported -42.49. Nothing in a"
        " sandbox catches that, so the marking is the deliverable.",
    )
    p.append("</svg>")
    return "\n".join(p) + "\n"


# --- 5. roadmap: horizons -----------------------------------------------------


@dataclass(frozen=True)
class Horizon:
    label: str
    window: str
    thesis: str
    items: tuple[str, ...]
    accent_index: int


HORIZONS = (
    Horizon(
        "Now", "shipped",
        "One person, one machine, numbers that reproduce.",
        (
            "37 tools across five families",
            "Ten agent roles, three validation tiers",
            "Real prices, rates, factors, uploads",
            "Web search, documents, MCP tools as context",
            "Manifests, re-run, exports, telemetry",
            "A sandbox for when no tool fits",
        ),
        2,
    ),
    Horizon(
        "Medium term", "the next two to four phases",
        "Make the workbench something a team can rely on.",
        (
            "Panel data and cross-sectional asset pricing",
            "A job queue, so a GARCH does not hold the request",
            "Notebook and Python export of a run",
            "Multi-user, projects with owners",
            "A tool authoring kit, so the registry is extensible",
            "Backtesting and portfolio construction",
            "Scheduled runs and drift alerts",
            "Narrator context, done safely",
        ),
        0,
    ),
    Horizon(
        "Long term", "the shape it is aiming at",
        "Make reproducibility the unit of exchange, not the report.",
        (
            "A shareable, verifiable result format",
            "A registry others can publish tools into",
            "Cross-study memory: what this desk already knows",
            "Continuous market efficiency monitoring",
            "A grounding gate that reads charts and tables too",
            "Regulatory-grade audit export",
        ),
        6,
    ),
)


def build_horizons(t: Theme) -> str:
    height = 560
    p = open_svg(
        W, height, t,
        title="Econometrica roadmap horizons",
        label="Three roadmap horizons: what is shipped, the medium term, and the long term",
    )
    p += header(
        t,
        "Where this goes",
        "Three horizons. Each one is only worth building because the one before it holds.",
    )

    top = 126
    card_w, xs = columns(W, MARGIN, 3, gap=22)
    card_h = 340

    for horizon, x in zip(HORIZONS, xs, strict=True):
        accent = t.series[horizon.accent_index]
        p.append(rect(x, top, card_w, card_h, fill=t.card, stroke=t.border))
        p.append(rect(x, top, 3, card_h, fill=accent, r=1.5))
        p.append(text_el(x + 18, top + 34, horizon.label, fill=t.text, size=19, weight="700"))
        p.append(
            text_el(x + card_w - 18, top + 34, horizon.window, fill=accent, size=12,
                    weight="600", anchor="end")
        )
        cursor = top + 58
        for prose in wrap(horizon.thesis, 46):
            p.append(text_el(x + 18, cursor, prose, fill=t.muted, size=12.5))
            cursor += 17
        cursor += 10
        for item in horizon.items:
            p.append(rect(x + 18, cursor - 8, 5, 5, fill=accent, r=2.5))
            for row, prose in enumerate(wrap(item, 42)):
                p.append(text_el(x + 32, cursor + row * 15, prose, fill=t.text, size=12))
            cursor += 15 * max(1, len(wrap(item, 42))) + 9

        if x != xs[-1]:
            p.append(arrow_right(x + card_w + 4, top + card_h / 2, 14, stroke=t.faint, width=1.6))

    p += footer(
        t, height - 26,
        "Nothing here loosens the invariant. A roadmap item that would let a model compute a"
        " statistic is not a later version of this product, it is a different one.",
    )
    p.append("</svg>")
    return "\n".join(p) + "\n"


# --- 6. the art of the possible -----------------------------------------------


@dataclass(frozen=True)
class Rung:
    title: str
    unlocked: str
    because: str


RUNGS = (
    Rung(
        "A number you can check",
        "A single result with a manifest under it.",
        "This is the rung everything else stands on, and it is the one that is built.",
    ),
    Rung(
        "A study that survives its author",
        "Anyone can re-run it a year later and be told, per step, what moved.",
        "Because the manifest names the data fingerprint, the tool version and the source.",
    ),
    Rung(
        "A desk with a memory",
        "Every question the team ever asked, with the evidence, searchable.",
        "Because a run is a row, not a conversation, and its artifacts are structured.",
    ),
    Rung(
        "Analysis that watches itself",
        "Efficiency scores and betas recomputed on a schedule, with alerts when they move.",
        "Because a run is already re-runnable without a model in the loop.",
    ),
    Rung(
        "A market for method",
        "Tools published, versioned and cited by people who did not write this application.",
        "Because a tool is a typed function with a version, not a paragraph in a notebook.",
    ),
)


def build_possible(t: Theme) -> str:
    height = 600
    p = open_svg(
        W, height, t,
        title="The art of the possible",
        label="Five rungs of capability, each one made reachable by the one below it",
    )
    p += header(
        t,
        "The art of the possible",
        "Each rung is only reachable because the one under it is mechanical rather than a promise.",
    )

    top = 128
    rung_h = 72
    gap = 14
    step = 46

    for index, rung in enumerate(reversed(RUNGS)):
        level = len(RUNGS) - 1 - index
        y = top + index * (rung_h + gap)
        x = MARGIN + level * step
        width = W - MARGIN - x
        accent = t.series[[2, 0, 6, 3, 1][level]]

        p.append(rect(x, y, width, rung_h, fill=t.card, stroke=t.border, r=8))
        p.append(rect(x, y, 3, rung_h, fill=accent, r=1.5))
        p.append(
            text_el(x + 18, y + 27, f"{level + 1}", fill=accent, size=15, weight="700")
        )
        p.append(text_el(x + 40, y + 27, rung.title, fill=t.text, size=15, weight="600"))
        p.append(text_el(x + 40, y + 47, rung.unlocked, fill=t.muted, size=12.5))
        p.append(
            text_el(x + width - 18, y + 47, rung.because, fill=t.faint, size=11.5, anchor="end")
        )

        if index < len(RUNGS) - 1:
            below_x = MARGIN + (level - 1) * step
            p.append(
                elbow(
                    [(below_x + 24, y + rung_h + gap), (below_x + 24, y + rung_h + 4),
                     (x + 12, y + rung_h + 4)],
                    stroke=t.faint, width=1.3, dash="3 4",
                )
            )

    p += footer(
        t, height - 26,
        "The interesting thing about this ladder is that the hard rung is the first one. Everything"
        " above it is engineering; the first one is a design decision nobody can retrofit.",
    )
    p.append("</svg>")
    return "\n".join(p) + "\n"


DIAGRAMS = {
    "doc-why-trust-gap": build_trust_gap,
    "doc-product-workbench": build_workbench,
    "doc-architecture-layers": build_layers,
    "doc-decision-central": build_central_decision,
    "doc-roadmap-horizons": build_horizons,
    "doc-art-of-the-possible": build_possible,
}


def main() -> None:
    here = Path(__file__).resolve().parent
    for stem, build in DIAGRAMS.items():
        for path in write_pair(here, stem, build):
            print(f"wrote {path.relative_to(here.parents[1])}")


if __name__ == "__main__":
    main()
