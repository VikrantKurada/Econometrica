"""Shared drawing primitives for the repository's SVG diagrams.

Extracted from ``build_capability_map.py`` when a second generator appeared.
The reason is the one already written into that file: two copies of the same
theme is how a light and a dark diagram drift apart, and the moment there were
two scripts the same risk applied across them.

Three constraints shape everything here, and none of them is taste:

* **No ``<style>`` block and no external font.** GitHub sanitises the SVG it
  renders inside a Markdown file, and a stylesheet or a webfont is the part
  that silently does not survive. Every property is a presentation attribute.
* **SVG text does not wrap, elide, or measure.** A line too long for its box
  runs off the edge and is simply lost, so line breaks are decided here by the
  caller and :func:`wrap` exists to make that cheap.
* **Two files, one definition.** Every drawing is a function of a
  :class:`Theme`, and :func:`write_pair` renders it twice. The README and the
  documentation pages serve the pair through ``<picture>`` so GitHub picks by
  the reader's own theme.

The series hexes are the project's own, copied from
``frontend/src/components/charts/palette.ts``. They are the exact steps the
colour-blindness separations were validated on, so they are not re-derived.
"""

from __future__ import annotations

import html
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

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

THEMES: tuple[Theme, ...] = (LIGHT, DARK)


@dataclass
class Card:
    """A titled box: a heading, some prose lines, some monospace lines."""

    title: str
    lines: list[str] = field(default_factory=list)
    mono: list[str] = field(default_factory=list)
    badge: str = ""


# --- primitives ---------------------------------------------------------------


def esc(text: str) -> str:
    return html.escape(text, quote=False)


def text_el(
    x: float,
    y: float,
    content: str,
    *,
    fill: str,
    size: float,
    weight: str = "400",
    family: str = SANS,
    anchor: str = "start",
    spacing: str = "0",
) -> str:
    return (
        f'<text x="{x}" y="{y}" fill="{fill}" font-family="{family}"'
        f' font-size="{size}" font-weight="{weight}" text-anchor="{anchor}"'
        f' letter-spacing="{spacing}">{esc(content)}</text>'
    )


def rect(
    x: float, y: float, w: float, h: float, *, fill: str, stroke: str = "none", r: float = 10
) -> str:
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}"'
        f' fill="{fill}" stroke="{stroke}"/>'
    )


def line(x1: float, y1: float, x2: float, y2: float, *, stroke: str, width: float = 1.4,
         dash: str = "") -> str:
    dasharray = f' stroke-dasharray="{dash}"' if dash else ""
    return (
        f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{stroke}"'
        f' stroke-width="{width}" stroke-linecap="round"{dasharray}/>'
    )


def arrow_right(x: float, y: float, length: float, *, stroke: str, width: float = 1.4) -> str:
    """A horizontal arrow drawn as one path, head included.

    A path rather than a ``<marker>``: markers are one of the things GitHub's
    SVG sanitiser has been observed to drop, and an arrow that loses its head
    reads as a divider.
    """
    return (
        f'<path d="M{x} {y} h{length} m-6 -4.5 l6 4.5 l-6 4.5" fill="none"'
        f' stroke="{stroke}" stroke-width="{width}" stroke-linecap="round"'
        f' stroke-linejoin="round"/>'
    )


def arrow_down(x: float, y: float, length: float, *, stroke: str, width: float = 1.4) -> str:
    return (
        f'<path d="M{x} {y} v{length} m-4.5 -6 l4.5 6 l4.5 -6" fill="none"'
        f' stroke="{stroke}" stroke-width="{width}" stroke-linecap="round"'
        f' stroke-linejoin="round"/>'
    )


def elbow(points: Sequence[tuple[float, float]], *, stroke: str, width: float = 1.4,
          dash: str = "") -> str:
    """A polyline through the given points, no arrowhead."""
    path = " ".join(
        ("M" if index == 0 else "L") + f"{x} {y}" for index, (x, y) in enumerate(points)
    )
    dasharray = f' stroke-dasharray="{dash}"' if dash else ""
    return (
        f'<path d="{path}" fill="none" stroke="{stroke}" stroke-width="{width}"'
        f' stroke-linecap="round" stroke-linejoin="round"{dasharray}/>'
    )


def band_heading(t: Theme, x: float, y: float, label: str, accent: str) -> list[str]:
    """A section label with a coloured tick, used to separate bands of a page."""
    return [
        rect(x, y - 9, 3, 12, fill=accent, r=1.5),
        text_el(x + 12, y, label.upper(), fill=t.muted, size=12, weight="700", spacing="1.4"),
    ]


def draw_card(
    t: Theme, card: Card, x: float, y: float, w: float, h: float, accent: str
) -> list[str]:
    """A card with an accent hairline down its left edge.

    A hairline rather than a filled header: the card has to stay legible at
    README width, where a tinted block swallows the text inside it.
    """
    out = [rect(x, y, w, h, fill=t.card, stroke=t.border)]
    out.append(rect(x, y, 3, h, fill=accent, r=1.5))
    cursor = y + 26
    out.append(text_el(x + 16, cursor, card.title, fill=t.text, size=15, weight="600"))
    if card.badge:
        out.append(
            text_el(
                x + w - 16, cursor, card.badge, fill=accent, size=15,
                weight="700", anchor="end",
            )
        )
    cursor += 20
    for prose in card.lines:
        out.append(text_el(x + 16, cursor, prose, fill=t.muted, size=12.5))
        cursor += 17
    if card.mono:
        cursor += 2
        for name in card.mono:
            out.append(text_el(x + 16, cursor, name, fill=t.faint, size=11.5, family=MONO))
            cursor += 15
    return out


def chip(t: Theme, x: float, y: float, w: float, h: float, label: str, accent: str,
         *, size: float = 13.5) -> list[str]:
    """A single-line labelled box. The unit most of these diagrams are made of."""
    return [
        rect(x, y, w, h, fill=t.card, stroke=t.border, r=8),
        rect(x, y, 3, h, fill=accent, r=1.5),
        text_el(
            x + w / 2 + 2, y + h / 2 + size * 0.36, label, fill=t.text, size=size,
            weight="600", anchor="middle",
        ),
    ]


def columns(width: float, margin: float, count: int, gap: float = 16) -> tuple[float, list[float]]:
    """Equal columns across a page: the column width, and each left edge."""
    col = (width - 2 * margin - gap * (count - 1)) / count
    return col, [margin + index * (col + gap) for index in range(count)]


def wrap(text: str, limit: int) -> list[str]:
    """Greedy wrap at a character count.

    Characters rather than measured width because there is no text metric
    available here, and the fonts are a fallback stack that differs per reader
    anyway. The limits in each diagram were arrived at by looking at the
    rendered output, which is the only check that means anything.
    """
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if len(candidate) > limit and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def paragraph(
    t: Theme, x: float, y: float, text: str, *, limit: int, size: float = 12.5,
    leading: float = 17, fill: str | None = None,
) -> list[str]:
    """Wrapped prose as a stack of ``<text>`` elements."""
    colour = t.muted if fill is None else fill
    return [
        text_el(x, y + index * leading, chunk, fill=colour, size=size)
        for index, chunk in enumerate(wrap(text, limit))
    ]


def open_svg(width: float, height: float, t: Theme, *, title: str, label: str) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}"'
        f' width="{width}" height="{height}" role="img" aria-label="{esc(label)}">',
        f"<title>{esc(title)}</title>",
        rect(0, 0, width, height, fill=t.bg, r=0),
    ]


def write_pair(
    directory: Path, stem: str, build: Callable[[Theme], str], themes: Iterable[Theme] = THEMES
) -> list[Path]:
    """Render one drawing once per theme, to ``<stem>-<theme>.svg``."""
    written: list[Path] = []
    for theme in themes:
        path = directory / f"{stem}-{theme.name}.svg"
        path.write_text(build(theme), encoding="utf-8")
        written.append(path)
    return written
