"""Generates the repository banner: what Econometrica can do, on one page.

Two files come out of one definition — a light and a dark variant — because the
README serves them through a `<picture>` element and GitHub picks by the
reader's theme. Hand-maintaining two copies of the same diagram is how they
drift, which is the same reason `palette.test.ts` asserts the CSS and the
TypeScript palettes agree.

The series hexes are the project's own, copied from
`frontend/src/components/charts/palette.ts`. They are the exact steps the
colour-blindness separations were validated on, so they are not re-derived here.

No `<style>` block and no external font: GitHub sanitises SVG it renders in a
README, and a stylesheet or a webfont is the part that silently does not
survive. Everything is a presentation attribute on the element itself.

It also emits the 1280x640 social-preview card GitHub shows when the repo is
linked elsewhere. That is a separate drawing rather than a crop of the map: an
unfurl renders around 400px wide, where the map's 12px body text lands under
4px and none of it is readable. The card carries five numbers at a size that
survives the scaling and nothing else.

Regenerate after editing:

    uv run python docs/assets/build_capability_map.py

Then rasterise the card, because GitHub's social preview takes an image upload
rather than an SVG. The renderer lives under ``frontend/`` because ESM resolves
a bare import from the importing module's own directory, and that is where the
only ``node_modules`` is:

    cd frontend && node scripts/render-social.mjs
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from pathlib import Path

W, H = 1440, 1020
MARGIN = 40
SANS = "ui-sans-serif,-apple-system,Segoe UI,Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"


@dataclass(frozen=True)
class Theme:
    name: str
    bg: str
    card: str
    border: str
    text: str
    muted: str
    faint: str
    series: tuple[str, ...]


LIGHT = Theme(
    name="light",
    bg="#ffffff",
    card="#fafafa",
    border="#e3e7ec",
    text="#14181d",
    muted="#5b6675",
    faint="#8d97a5",
    series=(
        "#2a78d6", "#eb6834", "#1baf7a", "#eda100",
        "#e87ba4", "#008300", "#4a3aa7", "#e34948",
    ),
)

DARK = Theme(
    name="dark",
    bg="#0f1319",
    card="#171c24",
    border="#272e39",
    text="#e8ecf1",
    muted="#93a0b1",
    faint="#6f7c8d",
    series=(
        "#3987e5", "#d95926", "#199e70", "#c98500",
        "#d55181", "#008300", "#9085e9", "#e66767",
    ),
)


@dataclass
class Card:
    title: str
    lines: list[str] = field(default_factory=list)
    mono: list[str] = field(default_factory=list)
    badge: str = ""


# --- content ------------------------------------------------------------------

SOURCES = [
    Card("Market prices", ["yfinance, dividend-adjusted", "daily closes, cached on disk"]),
    Card("Risk-free rates", ["FRED, 17 treasury series,", "de-annualised by compounding"]),
    # One mono line, not three: at this card height a third overflowed the
    # bottom edge and `carhart4` was clipped out of the picture entirely.
    Card("Factor sets", ["Ken French library"], mono=["ff3 · ff5 · carhart4"]),
    Card("Your own files", ["CSV, XLSX, Parquet: profiled,", "mapped, then a hypertable"]),
    Card("Synthetic", ["reproducible walks, no network.", "Every run flagged as generated"]),
]

AGENTS = ["Planner", "Data Steward", "Econometrician", "Validator", "Narrator", "Visualizer"]

FAMILIES = [
    Card(
        "Asset pricing",
        badge="7",
        mono=["capm", "ff3", "ff5", "carhart4", "fama_macbeth", "grs_test", "rolling_beta"],
    ),
    Card(
        "Market efficiency",
        badge="10",
        mono=[
            "adf", "kpss", "phillips_perron", "variance_ratio", "acf",
            "ljung_box", "runs_test", "bds", "hurst", "weak_form_score",
        ],
    ),
    Card(
        "Volatility and risk",
        badge="11",
        mono=[
            "garch", "egarch", "gjr_garch", "realized_vol", "ewma_vol",
            "historical_var", "parametric_var", "cvar", "drawdown",
            "kupiec_test", "christoffersen_test",
        ],
    ),
    Card(
        "Multivariate",
        badge="8",
        mono=[
            "var_model", "irf", "fevd", "johansen", "engle_granger",
            "vecm", "granger_causality", "markov_switching",
        ],
    ),
    Card("Event study", badge="1", mono=["event_study"]),
]

OUTPUTS = [
    Card("Charts", ["14 spec types, chosen from the", "result's shape. Light and dark,", "each with a table view"]),
    Card("Canvas", ["A tab per chart, plus Narrative,", "Diagnostics, a Trace DAG", "and a Cost dashboard"]),
    Card("Exports", ["JSON, Markdown, CSV, XLSX, ZIP.", "PNG and SVG from the live chart.", "PDF from a print stylesheet"]),
    Card("Reproducibility", ["A manifest under every result.", "Re-run re-executes the plan", "and consults no model"]),
    Card("Telemetry", ["Spans for latency, run steps for", "tokens and cost. No number is", "summed from both"]),
]

GUARDRAILS = [
    ("LLMs never compute statistics", "they select from the registry; the tools compute"),
    ("Tools refuse work the data cannot support", "preconditions are executable, not advice"),
    ("Prose is checked against the results", "an unmatched number withholds the whole narration"),
    ("Diagnostics are tri-state", "“not judged” is never “failed”"),
    ("Generated data and generated code are marked", "in the manifest, the banner and the printout"),
]

NOT_WIRED = (
    "Built and tested, not yet reachable from a run: "
    "web search · document retrieval over pgvector · the MCP tool allowlist"
)


# --- drawing ------------------------------------------------------------------


def esc(text: str) -> str:
    return html.escape(text, quote=False)


def text_el(
    x: float, y: float, content: str, *, fill: str, size: float,
    weight: str = "400", family: str = SANS, anchor: str = "start",
    spacing: str = "0",
) -> str:
    return (
        f'<text x="{x}" y="{y}" fill="{fill}" font-family="{family}"'
        f' font-size="{size}" font-weight="{weight}" text-anchor="{anchor}"'
        f' letter-spacing="{spacing}">{esc(content)}</text>'
    )


def rect(x: float, y: float, w: float, h: float, *, fill: str, stroke: str = "none", r: float = 10) -> str:
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}"'
        f' fill="{fill}" stroke="{stroke}"/>'
    )


def band_heading(t: Theme, x: float, y: float, label: str, accent: str) -> list[str]:
    return [
        rect(x, y - 9, 3, 12, fill=accent, r=1.5),
        text_el(x + 12, y, label.upper(), fill=t.muted, size=12, weight="700", spacing="1.4"),
    ]


def draw_card(t: Theme, card: Card, x: float, y: float, w: float, h: float, accent: str) -> list[str]:
    out = [rect(x, y, w, h, fill=t.card, stroke=t.border)]
    # A hairline in the band's colour rather than a filled header: the card has
    # to stay legible at README width, where a tinted block would swallow text.
    out.append(rect(x, y, 3, h, fill=accent, r=1.5))
    cursor = y + 26
    out.append(text_el(x + 16, cursor, card.title, fill=t.text, size=15, weight="600"))
    if card.badge:
        out.append(
            text_el(x + w - 16, cursor, card.badge, fill=accent, size=15, weight="700", anchor="end")
        )
    cursor += 20
    for line in card.lines:
        out.append(text_el(x + 16, cursor, line, fill=t.muted, size=12.5))
        cursor += 17
    if card.mono:
        cursor += 2
        for name in card.mono:
            out.append(text_el(x + 16, cursor, name, fill=t.faint, size=11.5, family=MONO))
            cursor += 15
    return out


def columns(count: int, gap: float = 16) -> tuple[float, list[float]]:
    width = (W - 2 * MARGIN - gap * (count - 1)) / count
    return width, [MARGIN + i * (width + gap) for i in range(count)]


def build(t: Theme) -> str:
    p: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}"'
        f' height="{H}" role="img" aria-label="Econometrica capability and feature map">',
        f"<title>Econometrica — capability and feature map</title>",
        rect(0, 0, W, H, fill=t.bg, r=0),
    ]

    # --- header
    p.append(text_el(MARGIN, 58, "Econometrica", fill=t.text, size=34, weight="700"))
    p.append(
        text_el(
            MARGIN + 232, 58,
            "a local econometrics workbench for asset pricing and market efficiency",
            fill=t.muted, size=15,
        )
    )
    p.append(rect(MARGIN, 78, W - 2 * MARGIN, 44, fill=t.card, stroke=t.border, r=8))
    p.append(rect(MARGIN, 78, 3, 44, fill=t.series[5], r=1.5))
    p.append(
        text_el(
            MARGIN + 16, 105,
            "The one invariant — LLMs never compute statistics. They select from a registry of "
            "37 typed, versioned tools; the tools compute.",
            fill=t.text, size=13.5,
        )
    )

    # --- data in
    y = 158
    p += band_heading(t, MARGIN, y, "Data in", t.series[0])
    w, xs = columns(5)
    for card, x in zip(SOURCES, xs, strict=True):
        p += draw_card(t, card, x, y + 16, w, 96, t.series[0])

    # --- agents
    y = 306
    p += band_heading(t, MARGIN, y, "Multi-agent pipeline", t.series[6])
    chip_y = y + 18
    chip_h = 38
    chip_w = (W - 2 * MARGIN - 5 * 34) / 6
    for i, role in enumerate(AGENTS):
        x = MARGIN + i * (chip_w + 34)
        p.append(rect(x, chip_y, chip_w, chip_h, fill=t.card, stroke=t.border, r=8))
        p.append(rect(x, chip_y, 3, chip_h, fill=t.series[6], r=1.5))
        p.append(
            text_el(x + chip_w / 2 + 2, chip_y + 24, role, fill=t.text, size=13.5,
                    weight="600", anchor="middle")
        )
        if i < len(AGENTS) - 1:
            ax = x + chip_w + 10
            p.append(
                f'<path d="M{ax} {chip_y + chip_h / 2} h14 m-5 -4 l5 4 l-5 4"'
                f' fill="none" stroke="{t.faint}" stroke-width="1.4"'
                f' stroke-linecap="round" stroke-linejoin="round"/>'
            )
    # Two lines. SVG text does not wrap, so a single line of this length ran
    # off the right edge and lost its last clause -- which was the clause about
    # generated results being marked, the one that must not go missing.
    p.append(
        text_el(
            MARGIN, chip_y + chip_h + 24,
            "Validation tiers: single · critic · consensus      "
            "Providers: Ollama · Anthropic · OpenAI · Gemini · NVIDIA NIM",
            fill=t.muted, size=12.5,
        )
    )
    p.append(
        text_el(
            MARGIN, chip_y + chip_h + 42,
            "Plus a Quant Coder that writes code in an OS-sandboxed process when no tool fits — "
            "off by default, and its results are marked unvalidated everywhere they surface.",
            fill=t.muted, size=12.5,
        )
    )

    # --- core
    y = 452
    p += band_heading(t, MARGIN, y, "Econometrics core — 37 typed tools, five families", t.series[2])
    w, xs = columns(5)
    for card, x in zip(FAMILIES, xs, strict=True):
        p += draw_card(t, card, x, y + 16, w, 218, t.series[2])
    p.append(
        text_el(
            MARGIN, y + 254,
            "Beside them: a deterministic diagnostics engine (normality, heteroskedasticity, "
            "autocorrelation, ARCH effects, structural breaks) and executable preconditions that "
            "decline a fit the data cannot support.",
            fill=t.muted, size=12.5,
        )
    )

    # --- outputs
    y = 748
    p += band_heading(t, MARGIN, y, "What comes out", t.series[1])
    w, xs = columns(5)
    for card, x in zip(OUTPUTS, xs, strict=True):
        p += draw_card(t, card, x, y + 16, w, 96, t.series[1])

    # --- guardrails
    y = 898
    p += band_heading(t, MARGIN, y, "How it avoids making numbers up", t.series[5])
    gy = y + 20
    gw = (W - 2 * MARGIN - 4 * 12) / 5
    for i, (head, tail) in enumerate(GUARDRAILS):
        x = MARGIN + i * (gw + 12)
        p.append(rect(x, gy, gw, 52, fill=t.card, stroke=t.border, r=8))
        p.append(text_el(x + 12, gy + 21, head, fill=t.text, size=11.5, weight="600"))
        p.append(text_el(x + 12, gy + 38, tail, fill=t.faint, size=10.5))

    # --- honest footer
    p.append(text_el(MARGIN, 1000, NOT_WIRED, fill=t.faint, size=11.5))
    p.append(
        text_el(
            W - MARGIN, 1000,
            "1417 backend tests · 320 frontend · 6 end-to-end · "
            "Python + FastAPI · React + TypeScript · Postgres, TimescaleDB, pgvector",
            fill=t.faint, size=11.5, anchor="end",
        )
    )

    p.append("</svg>")
    return "\n".join(p) + "\n"


# --- social preview -----------------------------------------------------------

SW, SH = 1280, 640
SMARGIN = 56

#: Big enough to survive the scaling an unfurl applies. Five is the most that
#: fit at this size, and they are the five that say what the product is.
STATS = [
    ("37", "typed tools"),
    ("5", "tool families"),
    ("6", "agent roles"),
    ("14", "chart types"),
    ("5", "LLM providers"),
]


def build_social(t: Theme) -> str:
    p: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SW} {SH}" width="{SW}"'
        f' height="{SH}" role="img" aria-label="Econometrica — a local econometrics workbench">',
        "<title>Econometrica</title>",
        rect(0, 0, SW, SH, fill=t.bg, r=0),
    ]

    p.append(text_el(SMARGIN, 130, "Econometrica", fill=t.text, size=82, weight="700"))
    p.append(
        text_el(
            SMARGIN, 176,
            "A local econometrics workbench for asset pricing and market efficiency",
            fill=t.muted, size=28,
        )
    )

    p.append(rect(SMARGIN, 218, SW - 2 * SMARGIN, 104, fill=t.card, stroke=t.border, r=12))
    p.append(rect(SMARGIN, 218, 5, 104, fill=t.series[5], r=2.5))
    p.append(
        text_el(SMARGIN + 28, 262, "LLMs never compute statistics.", fill=t.text, size=30, weight="600")
    )
    p.append(
        text_el(
            SMARGIN + 28, 298,
            "They select from a registry of typed, versioned tools — the tools compute.",
            fill=t.muted, size=26,
        )
    )

    gap = 18
    cw = (SW - 2 * SMARGIN - gap * (len(STATS) - 1)) / len(STATS)
    for i, (value, label) in enumerate(STATS):
        x = SMARGIN + i * (cw + gap)
        p.append(rect(x, 360, cw, 108, fill=t.card, stroke=t.border, r=12))
        p.append(
            text_el(x + cw / 2, 418, value, fill=t.series[i], size=46, weight="700", anchor="middle")
        )
        p.append(
            text_el(x + cw / 2, 448, label, fill=t.muted, size=20, anchor="middle")
        )

    p.append(
        text_el(
            SMARGIN, 528,
            "Every number traces to a tested function with a reproducibility manifest.",
            fill=t.text, size=26,
        )
    )
    # Kept short deliberately: the full clause ("· prose is checked against the
    # results") ran into the stack line on the right, and SVG text does not
    # wrap or elide -- it just overlaps.
    p.append(
        text_el(
            SMARGIN, 566,
            "Tools refuse work the data cannot support",
            fill=t.faint, size=22,
        )
    )
    p.append(
        text_el(
            SW - SMARGIN, 566,
            "Python · FastAPI · React · TimescaleDB · pgvector",
            fill=t.faint, size=22, anchor="end",
        )
    )

    p.append("</svg>")
    return "\n".join(p) + "\n"


def main() -> None:
    here = Path(__file__).resolve().parent
    for theme in (LIGHT, DARK):
        path = here / f"capability-map-{theme.name}.svg"
        path.write_text(build(theme), encoding="utf-8")
        print(f"wrote {path.relative_to(here.parents[1])}")

        card = here / f"social-preview-{theme.name}.svg"
        card.write_text(build_social(theme), encoding="utf-8")
        print(f"wrote {card.relative_to(here.parents[1])}")


if __name__ == "__main__":
    main()
