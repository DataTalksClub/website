"""The drawn knowledge graph is a drawing, not a pile of overlapping labels.

Both surfaces that draw the wiki as a hub and a ring — the homepage band and
`/wiki/graph` — lay their nodes out with `core.graph_layout`.  The failure the
positioned-pill drawing had was geometric: a long label grew its node until it
covered its neighbour, or hung off the edge of the frame.  These tests hold the
geometry itself, for the real data each page draws and for labels far longer
than anything the projection carries today.
"""

from __future__ import annotations

from django.test import SimpleTestCase, TestCase

from content.wiki_content import busiest_neighbourhood
from core.graph_layout import (
    NARROW_WRAP_OVER,
    RING_SPOKES,
    WIDE_WRAP_OVER,
    GraphLayout,
    GraphPoint,
    label_em,
    ring_layouts,
)
from core.home_content import wiki_graph


def boxes(layout: GraphLayout) -> tuple[tuple[float, float, float, float, str], ...]:
    """Every drawn pill as (left, top, width, height, label)."""

    return tuple(
        (node.left, node.top, node.width, node.height, " ".join(line.text for line in node.lines))
        for node in (*layout.nodes, layout.hub)
    )


def drawing_violations(layouts: tuple[GraphLayout, ...]) -> list[str]:
    """Every way a drawing fails to hold together: an escape or a collision."""

    violations: list[str] = []
    for layout in layouts:
        drawn = boxes(layout)
        for left, top, width, height, label in drawn:
            if left < 0 or top < 0 or left + width > layout.width or top + height > layout.height:
                violations.append(f"{layout.kind}: {label} leaves the frame")
        for index, first in enumerate(drawn):
            for second in drawn[index + 1 :]:
                if (
                    first[0] < second[0] + second[2]
                    and second[0] < first[0] + first[2]
                    and first[1] < second[1] + second[3]
                    and second[1] < first[1] + first[3]
                ):
                    violations.append(f"{layout.kind}: {first[4]} overlaps {second[4]}")
    return violations


class GraphGeometryTests(SimpleTestCase):
    def test_a_label_longer_than_its_pill_wraps_instead_of_widening_it(self) -> None:
        """A long title makes its node taller, never wider than its bound.

        This is what keeps a drawn node off its neighbours: before the wrap, one
        long title grew a pill across the middle of the ring.
        """

        spokes = tuple(
            GraphPoint(title="Retrieval Augmented Generation Evaluation", url=f"/wiki/{index}")
            for index in range(RING_SPOKES)
        )
        layouts = ring_layouts(
            GraphPoint(title="Machine Learning Operations", url="/wiki/mlops"),
            spokes,
            wide_wrap_over=WIDE_WRAP_OVER,
        )

        for layout in layouts:
            bound = WIDE_WRAP_OVER if layout.kind == "wide" else NARROW_WRAP_OVER
            for node in layout.nodes:
                with self.subTest(layout=layout.kind):
                    self.assertGreater(len(node.lines), 1)
                    self.assertLessEqual(node.width, bound)

    def test_a_single_unbreakable_word_keeps_its_own_line(self) -> None:
        point = GraphPoint(title="Antidisestablishmentarianism", url="/wiki/long")
        layouts = ring_layouts(point, (point,) * RING_SPOKES, wide_wrap_over=WIDE_WRAP_OVER)

        for layout in layouts:
            with self.subTest(layout=layout.kind):
                self.assertEqual(len(layout.nodes[0].lines), 1)
                self.assertEqual(layout.nodes[0].lines[0].text, point.title)

    def test_a_pill_is_as_wide_as_its_widest_line(self) -> None:
        layouts = ring_layouts(
            GraphPoint(title="MLOps", url="/wiki/mlops"),
            tuple(
                GraphPoint(title="Data Quality and Observability", url=f"/wiki/{index}")
                for index in range(RING_SPOKES)
            ),
            wide_wrap_over=WIDE_WRAP_OVER,
        )

        for layout in layouts:
            for node in layout.nodes:
                widest = max(label_em(line.text) for line in node.lines)
                with self.subTest(layout=layout.kind):
                    self.assertAlmostEqual(node.width, widest * node.font_size + 26.0, places=0)


class DrawnGraphDataTests(TestCase):
    """The drawings the two live pages actually make, from the checked data."""

    def test_neither_drawn_graph_overlaps_itself_or_leaves_its_frame(self) -> None:
        for name, layouts in (
            ("homepage", wiki_graph().layouts),
            ("wiki graph", busiest_neighbourhood().layouts),
        ):
            with self.subTest(page=name):
                self.assertEqual(drawing_violations(layouts), [])
