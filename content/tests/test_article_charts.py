"""Survey charts are drawn as static SVG bars from their typed block."""

from __future__ import annotations

from django.template.loader import render_to_string
from django.test import SimpleTestCase

from content.article_charts import BAR_SPAN, chart_view
from content.article_content import prose_sections

BLOCK = {
    "kind": "chart",
    "title": "Which tools do you use?",
    "unit": "percent",
    "caption": "Most use the first one.",
    "bars": [
        {"label": "First tool", "value": 61.5},
        {"label": "Second", "value": 25},
        {"label": "A third answer whose label is long enough to need two lines", "value": 4.0},
    ],
}


class ChartViewTests(SimpleTestCase):
    def test_bars_keep_source_order_values_and_scale_to_the_largest(self) -> None:
        chart = chart_view(BLOCK)
        assert chart is not None

        self.assertEqual([bar.label for bar in chart.bars], [row["label"] for row in BLOCK["bars"]])
        self.assertEqual([bar.value_text for bar in chart.bars], ["61.5%", "25%", "4.0%"])
        self.assertEqual(chart.bars[0].width, f"{BAR_SPAN:.2f}%")
        self.assertEqual(chart.bars[1].width, f"{25 / 61.5 * BAR_SPAN:.2f}%")

    def test_a_long_label_wraps_and_rows_never_overlap(self) -> None:
        chart = chart_view(BLOCK)
        assert chart is not None

        self.assertEqual(len(chart.bars[0].label_lines), 1)
        self.assertEqual(len(chart.bars[2].label_lines), 2)
        previous_bottom = -1
        for bar in chart.bars:
            first_label_y = bar.label_lines[0][0]
            self.assertGreater(first_label_y, previous_bottom)
            self.assertGreater(bar.bar_y, bar.label_lines[-1][0])
            previous_bottom = bar.bar_y + chart.bar_height
        self.assertGreaterEqual(chart.height, previous_bottom)

    def test_counts_are_written_without_a_percent_sign(self) -> None:
        chart = chart_view({**BLOCK, "unit": "count", "bars": [{"label": "Solo", "value": 117}]})
        assert chart is not None

        self.assertEqual(chart.bars[0].value_text, "117")
        self.assertIn("by respondents", chart.summary)

    def test_an_empty_chart_is_not_drawn(self) -> None:
        self.assertIsNone(chart_view({"kind": "chart", "title": "Empty", "bars": []}))
        self.assertEqual(prose_sections(({"kind": "chart", "bars": []},)), ())

    def test_rendered_chart_is_an_accessible_svg_figure_with_a_data_table(self) -> None:
        markup = render_to_string(
            "public/_prose_blocks.html", {"sections": prose_sections((BLOCK,))}
        )

        self.assertIn('<figure class="prose-chart">', markup)
        self.assertIn('<p class="prose-chart-title">Which tools do you use?</p>', markup)
        self.assertIn('role="img"', markup)
        self.assertIn(
            'aria-label="Which tools do you use? Horizontal bar chart of 3 answers by share '
            'of respondents; highest: First tool, 61.5%."',
            markup,
        )
        self.assertEqual(markup.count('class="prose-chart-bar"'), 3)
        self.assertIn('<table class="sr-only">', markup)
        self.assertIn('<th scope="row">Second</th>', markup)
        self.assertIn("<td>4.0%</td>", markup)
        self.assertIn("<figcaption>Most use the first one.</figcaption>", markup)
        self.assertNotIn("viewBox", markup)
        self.assertNotIn("prose-unavailable", markup)
        self.assertNotIn("<script", markup)
