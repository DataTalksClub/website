"""Static horizontal-bar geometry for the survey charts articles carry.

The import (``content.projection_helpers._article_chart_block``) reads each
legacy chart canvas into a typed block — a title, a caption, the unit and an
ordered list of labelled values.  This module turns one such block into what
``templates/public/_prose_blocks.html`` draws as inline SVG: one row per value,
its label on a line of its own above a bar, and the value written at the bar's
end.  No script draws anything.

The SVG carries no ``viewBox``: it fills the column, bar lengths and the value
positions are percentages of that width, and every row has a fixed pixel
height.  Scaling a whole drawing down to a phone would shrink its text with it;
this way the labels stay at body size at any width.  A label too long for a
narrow column wraps onto a second line here, because SVG text cannot wrap
itself.

Bars keep the author's order: several charts are ordinal (team sizes, how long
someone has used a tool, a model count), and the rest were written largest
first.  The longest bar spans ``BAR_SPAN`` percent of the width, which leaves
room for its value label on a phone-width column.

A chart the source drew as a pie (``shape: "pie"``) is drawn as a donut again,
with a legend of every answer and its value beside it, as long as its values
make one whole (they add up to about 100%).  A multiple-choice question whose
shares add up to well over 100% was never a whole, so it keeps the bars.  The
first ``PIE_TONES`` answers take the chart palette in order; an "Other" answer
and any answer past those take the neutral tone, never a generated hue.
"""

from __future__ import annotations

import math
import textwrap
from dataclasses import dataclass
from typing import Any

# Share of the drawing's width the longest bar spans; the rest holds its value.
BAR_SPAN = 82.0
# Row geometry in CSS pixels.  A label line is followed by the bar, whose value
# sits on the bar's own centre line.
LABEL_LINE = 18
LABEL_TO_BAR = 6
BAR_HEIGHT = 14
ROW_GAP = 14
# A label longer than this wraps; a phone column fits about this many
# characters of the label face before the edge.
LABEL_WRAP = 34


@dataclass(frozen=True, slots=True)
class ChartBar:
    """One labelled value, positioned in its chart."""

    label: str
    # Each label line with its text baseline, top to bottom.
    label_lines: tuple[tuple[int, str], ...]
    value_text: str
    bar_y: int
    value_y: int
    # Percent of the drawing's width, already formatted for an SVG attribute.
    width: str


# A pie is drawn only when its shares make one whole, give or take rounding.
PIE_WHOLE = (97.0, 103.0)
# Categorical tones a pie can use before the rest go neutral.
PIE_TONES = 5
# Donut geometry in viewBox units (the drawing is 100 x 100).
PIE_OUTER = 50.0
PIE_INNER = 30.0


@dataclass(frozen=True, slots=True)
class ChartSlice:
    """One answer of a pie: its wedge path and its legend entry."""

    label: str
    value_text: str
    # SVG path data in the 100 x 100 viewBox; empty for a zero share.
    path: str
    # "1".."PIE_TONES", or "rest" for the neutral tone.
    tone: str


@dataclass(frozen=True, slots=True)
class ArticleChart:
    """A survey chart as the page draws it."""

    title: str
    caption: str
    unit: str
    bars: tuple[ChartBar, ...]
    height: int
    bar_height: int = BAR_HEIGHT
    shape: str = "bar"
    slices: tuple[ChartSlice, ...] = ()

    @property
    def summary(self) -> str:
        """The chart's accessible name: what it shows and where it peaks.

        A pie names every answer with its value: its legend is drawn for the
        eye, so the drawing's name is where a screen reader hears the numbers.
        """

        title = self.title or "Survey chart"
        if not title.endswith((".", "?", "!")):
            title += "."
        if self.shape == "pie":
            answers = "; ".join(f"{part.label}, {part.value_text}" for part in self.slices)
            return f"{title} Pie chart of {len(self.slices)} answers: {answers}."
        top = max(self.bars, key=lambda bar: _number(bar.value_text))
        measure = "share of respondents" if self.unit == "percent" else "respondents"
        return (
            f"{title} Horizontal bar chart of {len(self.bars)} answers by {measure}; "
            f"highest: {top.label}, {top.value_text}."
        )


def _number(text: str) -> float:
    return float(text.rstrip("%"))


def _value_text(value: int | float, unit: str) -> str:
    # The source's own figure, as written: 25 stays 25 and 4.0 stays 4.0.
    written = repr(value) if isinstance(value, float) else str(value)
    return f"{written}%" if unit == "percent" else written


def chart_view(block: dict[str, Any]) -> ArticleChart | None:
    """Return one stored chart block as positioned bars, or ``None`` if empty."""

    unit = "count" if block.get("unit") == "count" else "percent"
    rows: list[tuple[str, int | float]] = []
    for bar in block.get("bars") or ():
        if not isinstance(bar, dict):
            continue
        label = str(bar.get("label") or "").strip()
        value = bar.get("value")
        if not label or isinstance(value, bool) or not isinstance(value, int | float):
            continue
        rows.append((label, value))
    if not rows:
        return None
    caption = str(block.get("caption") or "")
    title = str(block.get("title") or "")
    total = sum(value for _, value in rows)
    if block.get("shape") == "pie" and unit == "percent" and PIE_WHOLE[0] <= total <= PIE_WHOLE[1]:
        return ArticleChart(
            title=title,
            caption=caption,
            unit=unit,
            bars=(),
            height=0,
            shape="pie",
            slices=_pie_slices(rows, total, unit),
        )
    largest = max(value for _, value in rows) or 1
    bars: list[ChartBar] = []
    top = 0
    for label, value in rows:
        lines = tuple(textwrap.wrap(label, LABEL_WRAP)) or (label,)
        bar_y = top + LABEL_LINE * len(lines) + LABEL_TO_BAR
        bars.append(
            ChartBar(
                label=label,
                label_lines=tuple(
                    (top + LABEL_LINE * (index + 1) - 4, line) for index, line in enumerate(lines)
                ),
                value_text=_value_text(value, unit),
                bar_y=bar_y,
                value_y=bar_y + BAR_HEIGHT - 2,
                width=f"{value / largest * BAR_SPAN:.2f}%",
            )
        )
        top = bar_y + BAR_HEIGHT + ROW_GAP
    return ArticleChart(
        title=title,
        caption=caption,
        unit=unit,
        bars=tuple(bars),
        height=top - ROW_GAP + 2,
    )


def _point(radius: float, turn: float) -> str:
    # ``turn`` is a fraction of the circle, clockwise from twelve o'clock.
    angle = 2 * math.pi * turn - math.pi / 2
    return f"{50 + radius * math.cos(angle):.3f} {50 + radius * math.sin(angle):.3f}"


def _wedge(start: float, end: float) -> str:
    """Return the donut segment from ``start`` to ``end`` (fractions of a turn)."""

    if end - start >= 0.9999:
        # A full ring cannot be one arc; draw it as two halves.
        return _wedge(0.0, 0.5) + " " + _wedge(0.5, 1.0)
    large = 1 if end - start > 0.5 else 0
    return (
        f"M {_point(PIE_OUTER, start)} "
        f"A {PIE_OUTER:g} {PIE_OUTER:g} 0 {large} 1 {_point(PIE_OUTER, end)} "
        f"L {_point(PIE_INNER, end)} "
        f"A {PIE_INNER:g} {PIE_INNER:g} 0 {large} 0 {_point(PIE_INNER, start)} Z"
    )


def _pie_slices(
    rows: list[tuple[str, int | float]], total: float, unit: str
) -> tuple[ChartSlice, ...]:
    slices: list[ChartSlice] = []
    used = 0
    turn = 0.0
    for label, value in rows:
        if label.casefold().startswith("other") or used >= PIE_TONES:
            tone = "rest"
        else:
            used += 1
            tone = str(used)
        share = value / total
        path = _wedge(turn, turn + share) if share > 0 else ""
        turn += share
        slices.append(
            ChartSlice(label=label, value_text=_value_text(value, unit), path=path, tone=tone)
        )
    return tuple(slices)
