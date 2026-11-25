"""Survey charts are drawn as static SVG bars from their typed block."""

from __future__ import annotations

from typing import Any

from django.template.loader import render_to_string
from django.test import SimpleTestCase

from content import projection_helpers as builder
from content.article_charts import BAR_SPAN, chart_view
from content.article_content import prose_sections

BLOCK: dict[str, Any] = {
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
        # The caption sits under the framed card, outside it, like an image's.
        card_end = markup.index("</div>", markup.index('class="prose-chart-card"'))
        self.assertLess(card_end, markup.index("<figcaption>"))
        self.assertLess(markup.index("<figcaption>"), markup.index("</figure>"))
        self.assertNotIn("viewBox", markup)
        self.assertNotIn("prose-unavailable", markup)
        self.assertNotIn("<script", markup)


PIE: dict[str, Any] = {
    "kind": "chart",
    "title": "Roles",
    "unit": "percent",
    "shape": "pie",
    "bars": [
        {"label": "Data engineering", "value": 40},
        {"label": "Other", "value": 10},
        {"label": "Analytics", "value": 25},
        {"label": "Software", "value": 10},
        {"label": "Management", "value": 5},
        {"label": "Research", "value": 5},
        {"label": "Students", "value": 5},
    ],
}


class PieChartTests(SimpleTestCase):
    def test_a_source_pie_that_makes_a_whole_is_drawn_as_a_donut(self) -> None:
        chart = chart_view(PIE)
        assert chart is not None

        self.assertEqual(chart.shape, "pie")
        self.assertEqual(chart.bars, ())
        self.assertEqual([part.value_text for part in chart.slices][:3], ["40%", "10%", "25%"])
        # Tones in order, "Other" neutral, and the answer past five neutral too.
        self.assertEqual(
            [part.tone for part in chart.slices], ["1", "rest", "2", "3", "4", "5", "rest"]
        )
        self.assertTrue(all(part.path.startswith("M ") for part in chart.slices))
        self.assertEqual(
            chart.summary,
            "Roles. Pie chart of 7 answers: Data engineering, 40%; Other, 10%; Analytics, 25%; "
            "Software, 10%; Management, 5%; Research, 5%; Students, 5%.",
        )

    def test_shares_that_overlap_past_a_whole_keep_the_bars(self) -> None:
        chart = chart_view(
            {**PIE, "bars": [{"label": "A", "value": 70}, {"label": "B", "value": 60}]}
        )
        assert chart is not None

        self.assertEqual(chart.shape, "bar")
        self.assertEqual(len(chart.bars), 2)

    def test_a_single_whole_answer_is_a_full_ring(self) -> None:
        chart = chart_view({**PIE, "bars": [{"label": "Yes", "value": 100}]})
        assert chart is not None

        self.assertEqual(chart.slices[0].path.count("M "), 2)

    def test_rendered_pie_names_every_value_and_hides_the_visual_legend(self) -> None:
        markup = render_to_string(
            "public/_prose_blocks.html",
            {"sections": prose_sections(({**PIE, "caption": "Members from 65 countries."},))},
        )

        self.assertIn('class="prose-chart-donut" viewBox="0 0 100 100" role="img"', markup)
        self.assertIn('aria-label="Roles. Pie chart of 7 answers: Data engineering, 40%;', markup)
        self.assertEqual(markup.count('class="prose-chart-slice'), 7)
        self.assertIn('<ul class="prose-chart-legend" aria-hidden="true">', markup)
        self.assertNotIn('<table class="sr-only">', markup)
        # Both the pie row and the card close before the caption opens.
        between = markup[markup.index("</ul>") : markup.index("<figcaption>")]
        self.assertEqual(between.count("</div>"), 2)


class ChartGroupTests(SimpleTestCase):
    """Charts set side by side are one figure with one caption under the grid."""

    def test_a_run_of_charts_is_one_group_with_the_group_caption_once(self) -> None:
        caption = "Survey, 2025: roles and seniority."
        sections = prose_sections(
            (
                {**PIE, "title": "Roles"},
                {**PIE, "title": "Seniority"},
                {"kind": "chart_group_caption", "text": caption},
                {"kind": "paragraph", "text": "After."},
            )
        )

        self.assertEqual([section.kind for section in sections], ["chart_group", "paragraph"])
        self.assertEqual(len(sections[0].items), 2)
        markup = render_to_string("public/_prose_blocks.html", {"sections": sections})
        self.assertEqual(markup.count('<figure class="prose-chart">'), 2)
        self.assertEqual(markup.count("<figcaption>"), 1)
        # The caption closes the group after the whole grid, not under one card.
        grid_end = markup.index("</div>", markup.rindex('<figure class="prose-chart">'))
        self.assertLess(grid_end, markup.index("<figcaption>"))
        after_caption = markup[markup.index(f"<figcaption>{caption}</figcaption>") :]
        self.assertTrue(after_caption.split("</figcaption>", 1)[1].strip().startswith("</figure>"))

    def test_a_lone_chart_without_a_group_caption_stays_on_its_own(self) -> None:
        sections = prose_sections((BLOCK, {"kind": "paragraph", "text": "After."}))

        self.assertEqual([section.kind for section in sections], ["chart", "paragraph"])

    def test_the_import_reads_a_caption_after_the_charts_as_the_group_caption(self) -> None:
        canvas = (
            '<figure><canvas class="ai-chart" data-type="pie" data-title="{title}"'
            " data-labels='[\"A\", \"B\"]' data-values='[60, 40]'></canvas></figure>"
        )
        body = "\n\n".join(
            (
                '<div style="display: grid;">',
                canvas.format(title="One"),
                canvas.format(title="Two"),
                "<figcaption>Both, from the survey.</figcaption>",
                "</div>",
            )
        )

        blocks = builder._article_blocks(body, media_root=None, counters={})

        self.assertEqual(
            [block["kind"] for block in blocks], ["chart", "chart", "chart_group_caption"]
        )
        self.assertEqual(blocks[-1]["text"], "Both, from the survey.")

    def test_a_caption_chunk_that_follows_no_chart_stays_text(self) -> None:
        blocks = builder._article_blocks(
            "Text.\n\n<figcaption>Orphan.</figcaption>", media_root=None, counters={}
        )

        self.assertEqual([block["kind"] for block in blocks], ["paragraph", "paragraph"])
