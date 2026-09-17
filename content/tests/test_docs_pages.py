"""The two documentation pages a reader actually meets: the hub and a detail page.

``test_docs_reader`` owns the read model -- what pages exist, where they
live, and what their markdown rendered to.  This module owns what the two templates
make of that: how the hub groups 105 pages so they can be found, how search ranks
and explains a result, and how a detail page says where the reader is, what else is
in the guide, and where to go next.
"""

from __future__ import annotations

import re
from types import MappingProxyType, SimpleNamespace
from unittest.mock import patch

from django.test import TestCase

from content.docs_presentation import (
    docs_guide_sequence,
    docs_hub,
    docs_rail,
    docs_search_results,
)
from content.docs_reader import (
    DOCS_ROOT_PATH,
    DocsNavigationItem,
    DocsNavigationTree,
    docs_navigation_tree,
    docs_page,
)

FAMILY = "/docs/courses/ml-zoomcamp/"
LEAF = "/docs/courses/ml-zoomcamp/curriculum/"
DEEP = "/docs/general/guidelines/ai-usage/"


def three_level_tree() -> DocsNavigationTree:
    """A guide holding sections that hold pages -- the Zoomcamp Logistics shape."""

    def page(path: str, title: str, parent: str | None) -> dict[str, object]:
        return {
            "source_path": f"{path.strip('/').replace('/', '-') or 'index'}.md",
            "public_path": path,
            "title": title,
            "parent_path": parent,
        }

    return _tree(
        (
            page(DOCS_ROOT_PATH, "Documentation", None),
            page("/docs/area/", "Area", None),
            page("/docs/guide/", "Guide", "/docs/area/"),
            page("/docs/guide/section-a/", "Section A", "/docs/guide/"),
            page("/docs/guide/section-a/leaf-1/", "Leaf 1", "/docs/guide/section-a/"),
            page("/docs/guide/section-a/leaf-2/", "Leaf 2", "/docs/guide/section-a/"),
            page("/docs/guide/section-b/", "Section B", "/docs/guide/"),
        )
    )


def _tree(pages: tuple[dict[str, object], ...]) -> DocsNavigationTree:
    """Assemble one synthetic tree in the shape the read model hands templates.

    The real hierarchy is the shared knowledge base app's stored parent links;
    the presentation helpers under test only ever see the tree the read model
    builds from them, so a synthetic tree is built here rather than pulled
    through the database.
    """

    children: dict[str | None, list[dict[str, object]]] = {}
    for page in pages:
        raw_parent = page["parent_path"]
        parent = None if raw_parent is None else str(raw_parent)
        children.setdefault(None if parent == DOCS_ROOT_PATH else parent, []).append(page)
    by_path: dict[str, DocsNavigationItem] = {}

    def build(page: dict[str, object]) -> DocsNavigationItem:
        path = str(page["public_path"])
        item = DocsNavigationItem(
            page=MappingProxyType(dict(page)),
            children=tuple(build(child) for child in children.get(path, ())),
        )
        by_path[path] = item
        return item

    root_page = next(page for page in pages if page["public_path"] == DOCS_ROOT_PATH)
    root = DocsNavigationItem(
        page=MappingProxyType(dict(root_page)),
        children=tuple(build(child) for child in children.get(None, ()) if child is not root_page),
    )
    by_path[DOCS_ROOT_PATH] = root
    preorder: list[DocsNavigationItem] = []

    def visit(item: DocsNavigationItem) -> None:
        preorder.append(item)
        for child in item.children:
            visit(child)

    visit(root)
    ordered = tuple(preorder)
    return DocsNavigationTree(
        root=root,
        preorder=ordered,
        documents=ordered[1:],
        by_path=MappingProxyType(dict(by_path)),
    )


def hub_body(client) -> str:
    return page_body(client, DOCS_ROOT_PATH)


def page_body(client, path: str) -> str:
    response = client.get(path)
    assert response.status_code == 200, path
    return response.content.decode("utf-8").split("</head>", 1)[1]


class DocsHubTests(TestCase):
    def test_the_hub_puts_every_page_but_the_two_indexes_one_click_away(self) -> None:
        body = hub_body(self.client)
        linked = set(re.findall(r'href="(/docs/[^"]*)"', body))
        tree = docs_navigation_tree()

        unreachable = [
            item.public_path for item in tree.documents if item.public_path not in linked
        ]
        # The only pages the hub may leave unlinked are the area indexes whose
        # whole content is the list of children the hub now draws itself, and each
        # of those is still one crumb from every page beneath it.  Before this, 21
        # of 105 pages were one click from the hub and the certification page --
        # the most linked-to page in the corpus -- was three.
        areas = {item.public_path for item in tree.root.children}
        self.assertEqual([path for path in unreachable if path not in areas], [])
        self.assertGreaterEqual(len(tree.documents) - len(unreachable), len(tree.documents) - 2)

    def test_course_families_are_illustrated_cards_with_their_real_pages(self) -> None:
        hub = docs_hub(docs_navigation_tree())
        body = hub_body(self.client)

        self.assertTrue(hub.families)
        self.assertEqual(body.count("docs-course-card"), len(hub.families))
        for guide in hub.families:
            with self.subTest(public_path=guide.public_path):
                self.assertEqual(guide.page_count, len(guide.pages))
                self.assertIn(f'href="{guide.public_path}"', body)
                self.assertIn(f"{guide.page_count} page", body)
                for page in guide.pages:
                    self.assertIn(f'href="{page.public_path}"', body)
        # Every card carries a drawing: the hub used to be the one index page on
        # the site with no illustration at all, while the six course files sat on
        # disk for the homepage, the tour and the catalogue to draw.
        self.assertEqual(body.count("docs-course-figure"), len(hub.families))
        self.assertIn("doodle-dark", body)

    def test_each_family_card_draws_its_own_course_illustration(self) -> None:
        # The join between the two corpora is the course title, so it survives a
        # course being added and needs no slug map -- and it is lenient about a
        # plural, which is the whole difference between the documentation's "Stock
        # Market Analytics Zoomcamp" and the catalogue's "Stock Markets Analytics
        # Zoomcamp".  The local catalogue is empty in this fixture, so the join
        # itself is exercised against a stubbed one.
        tree = docs_navigation_tree()
        titles = [guide.title for guide in docs_hub(tree).families]
        self.assertTrue(titles)
        catalog = tuple(
            SimpleNamespace(title=title.replace("Zoomcamp", "Zoomcamps"), family=f"slug-{index}")
            for index, title in enumerate(titles)
        )
        with patch("core.home_content.course_catalog", return_value=catalog):
            families = docs_hub(tree).families
        self.assertEqual(
            [guide.family_slug for guide in families],
            [f"slug-{index}" for index in range(len(titles))],
        )

    def test_a_guide_with_sections_opens_its_own_chapter(self) -> None:
        tree = docs_navigation_tree()
        hub = docs_hub(tree)
        body = hub_body(self.client)

        for guide in hub.sectioned:
            with self.subTest(public_path=guide.public_path):
                self.assertTrue(any(section.pages for section in guide.sections))
                self.assertIn(f">{guide.title}</h2>", body)
                for section in guide.sections:
                    self.assertIn(f'href="{section.public_path}"', body)
        for guide in hub.platform:
            with self.subTest(public_path=guide.public_path):
                self.assertFalse(any(section.pages for section in guide.sections))

    def test_chapters_use_the_shared_band_head_not_a_local_restatement(self) -> None:
        body = hub_body(self.client)

        self.assertIn('class="docs-chapter docs-chapter-first', body)
        self.assertIn('class="band-head"', body)
        self.assertNotIn("docs-section-head", body)

    def test_an_area_holding_guides_becomes_one_row_for_each_of_them(self) -> None:
        hub = docs_hub(docs_navigation_tree())
        titles = [row.title for row in hub.community]

        # Activities is six flat pages, so it stays one row with its pages beside
        # it; General holds guides of its own, so drawing it as a single row would
        # hide their pages behind two titles.
        self.assertIn("Activities", titles)
        self.assertNotIn("General", titles)
        self.assertIn("Community Guidelines", titles)

    def test_the_hero_draws_the_reading_illustration_beside_the_title(self) -> None:
        body = hub_body(self.client)

        self.assertIn('class="docs-hero-art"', body)
        self.assertIn("course-learning", body)
        self.assertIn("doodle-light", body)


class DocsSearchTests(TestCase):
    def test_title_matches_rank_above_body_matches(self) -> None:
        results = docs_search_results("certificate")

        self.assertTrue(results)
        self.assertEqual(results[0].title, "Certificate")
        titles = [result.title for result in results]
        body_only = [
            index
            for index, result in enumerate(results)
            if "certificate" not in result.title.casefold()
        ]
        title_matches = [
            index
            for index, result in enumerate(results)
            if "certificate" in result.title.casefold()
        ]
        self.assertTrue(title_matches, titles)
        if body_only:
            self.assertLess(max(title_matches), min(body_only), titles)

    def test_every_result_carries_a_trail_and_a_marked_term(self) -> None:
        results = docs_search_results("certificate")

        for result in results:
            with self.subTest(public_path=result.public_path):
                # The root is the page the reader is already on, so the trail names
                # only the guide levels between it and the result.
                self.assertNotIn("Documentation /", result.trail)
                self.assertTrue(result.trail or result.public_path == DOCS_ROOT_PATH)
                marked = f"{result.title_html} {result.snippet_html}"
                self.assertIn("<mark>", marked)

    def test_marking_escapes_the_page_text_it_marks(self) -> None:
        for result in docs_search_results("a"):
            with self.subTest(public_path=result.public_path):
                self.assertNotIn("<script", result.snippet_html.casefold())
                unmarked = result.snippet_html.replace("<mark>", "").replace("</mark>", "")
                self.assertNotIn("<", unmarked)

    def test_results_render_as_one_link_per_row_under_a_band_head(self) -> None:
        response = self.client.get(DOCS_ROOT_PATH, query_params={"q": "certificate"})

        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8").split("</head>", 1)[1]
        rows = re.findall(
            r'<article class="list-row docs-row docs-result-row">.*?</article>', body, re.DOTALL
        )
        self.assertEqual(len(rows), len(response.context["docs_results"]))
        for row in rows:
            with self.subTest(row=row[:80]):
                self.assertEqual(row.count("<a "), 1)
        self.assertIn("<mark>", body)
        self.assertNotIn("Read guide", body)
        self.assertIn('class="band-head"', body)

    def test_the_search_field_is_labelled_and_shows_real_examples(self) -> None:
        response = self.client.get(DOCS_ROOT_PATH)

        self.assertContains(response, 'class="field-label" for="docs-query"')
        self.assertContains(
            response, 'placeholder="e.g. certificate, homework deadline, GCP credits"'
        )
        self.assertNotContains(response, '<label class="sr-only" for="docs-query">')


class DocsDetailNavigationTests(TestCase):
    def test_the_guide_rail_is_the_lesson_pages_module_rail(self) -> None:
        body = page_body(self.client, LEAF)

        # The component, not a copy of it: the same layout, rows, ordinals and
        # current state the course lesson pages already ship.
        self.assertIn('class="module-layout shell-breakout docs-layout', body)
        self.assertIn('class="module-sidebar module-rail docs-rail"', body)
        self.assertIn('class="rail-unit-link"', body)
        self.assertIn('class="read-indicator', body)
        # And none of the chrome it replaces.
        self.assertNotIn("docs-local-disclosure", body)
        self.assertNotIn("docs-tree-link", body)
        self.assertNotIn("docs-context-link", body)

    def test_the_rail_lists_the_guide_and_marks_the_page_inside_it(self) -> None:
        tree = docs_navigation_tree()
        rail = docs_rail(tree, LEAF)

        self.assertIsNotNone(rail)
        assert rail is not None
        self.assertEqual(rail.root.public_path, FAMILY)
        self.assertEqual(len(rail.units), len(tree.by_path[FAMILY].children))
        current = [unit for unit in rail.units if unit.is_current]
        self.assertEqual([unit.item.public_path for unit in current], [LEAF])

        body = page_body(self.client, LEAF)
        rail_markup = body.split('id="docs-rail"', 1)[1].split("</nav>", 1)[0]
        self.assertEqual(rail_markup.count('aria-current="page"'), 1)
        self.assertEqual(rail_markup.count('class="rail-unit-link"'), len(rail.units))

    def test_a_page_nested_below_its_guide_still_sees_the_whole_guide(self) -> None:
        # A Zoomcamp Logistics leaf used to see the five pages of its own section
        # and no trace of the guide those five sit in; the breadcrumb was the only
        # thing on the page that named it.  The reference corpus is two levels
        # shallower than the real one, so the three-level shape is built here.
        tree = three_level_tree()
        rail = docs_rail(tree, "/docs/guide/section-a/leaf-2/")

        assert rail is not None
        self.assertEqual(rail.root.public_path, "/docs/guide/")
        self.assertEqual(
            [unit.item.public_path for unit in rail.units],
            ["/docs/guide/section-a/", "/docs/guide/section-b/"],
        )
        opened = [unit for unit in rail.units if unit.children]
        self.assertEqual([unit.item.public_path for unit in opened], ["/docs/guide/section-a/"])
        self.assertEqual(
            [child.item.public_path for child in opened[0].children],
            ["/docs/guide/section-a/leaf-1/", "/docs/guide/section-a/leaf-2/"],
        )
        self.assertEqual(
            [child.is_current for child in opened[0].children],
            [False, True],
        )
        # One level of nesting, never the whole tree.
        for child in opened[0].children:
            self.assertEqual(child.children, ())

    def test_a_guide_page_is_its_own_rail_root(self) -> None:
        tree = docs_navigation_tree()
        rail = docs_rail(tree, FAMILY)

        assert rail is not None
        self.assertTrue(rail.root_is_current)
        self.assertFalse(any(unit.is_current for unit in rail.units))
        body = page_body(self.client, FAMILY)
        # On a phone a guide's own page opens with the list of pages in it.
        self.assertIn("docs-layout-guide", body)
        # The list is drawn once, not as a rail and again as a section below the
        # article.
        self.assertEqual(body.count('id="docs-rail"'), 1)
        self.assertNotIn("docs-child-link", body)

    def test_previous_and_next_continue_across_a_group_boundary(self) -> None:
        tree = three_level_tree()

        # Sibling order dead-ended at the last page of a group; guide order
        # carries on into the next group's own page.
        _previous, following = docs_guide_sequence(tree, "/docs/guide/section-a/leaf-2/")
        assert following is not None
        self.assertEqual(following["public_path"], "/docs/guide/section-b/")

        # It still stops at the guide's edge: inventing a next guide would be
        # worse than offering none.
        _previous, beyond = docs_guide_sequence(tree, "/docs/guide/section-b/")
        self.assertIsNone(beyond)

        # And the first page of a guide steps back to the guide itself, which is
        # the one way up the page needs beside its trail.
        previous, _following = docs_guide_sequence(tree, "/docs/guide/section-a/")
        assert previous is not None
        self.assertEqual(previous["public_path"], "/docs/guide/")

    def test_the_article_foot_offers_the_pager_and_the_edit_link(self) -> None:
        page = docs_page(LEAF)
        assert page is not None
        body = page_body(self.client, LEAF)

        self.assertIn('class="docs-pager"', body)
        self.assertIn("Previous", body)
        self.assertIn("Next", body)
        # `edit_url` has been in the page data since the first sync and was drawn
        # nowhere: a documentation page nobody can correct stays wrong.
        self.assertIn(f'href="{page["edit_url"]}"', body)
        self.assertIn("Edit this page on GitHub", body)

    def test_the_header_drops_the_eyebrow_that_repeated_the_crumb(self) -> None:
        tree = docs_navigation_tree()
        body = page_body(self.client, LEAF)
        parent_title = tree.by_path[FAMILY].title

        header = body.split('class="docs-detail-header"', 1)[1].split("</div>", 1)[0]
        self.assertNotIn(f'<p class="mono-label mono-label-indigo">{parent_title}</p>', header)
        self.assertIn("breadcrumbs", header)

    def test_on_this_page_opens_and_says_how_many_sections_it_holds(self) -> None:
        response = self.client.get(LEAF)
        sections = response.context["docs_sections"]
        body = response.content.decode("utf-8").split("</head>", 1)[1]

        if len(sections) > 2:
            self.assertIn('<details class="docs-on-page" open>', body)
            self.assertIn(f"{len(sections)} sections", body)
            for heading in sections:
                self.assertIn(f'href="#{heading["id"]}"', body)
        else:
            self.assertNotIn("docs-on-page", body)

    def test_every_docs_page_can_be_searched_from_where_the_reader_is(self) -> None:
        body = page_body(self.client, LEAF)

        self.assertIn('class="search-row docs-search-compact"', body)
        self.assertIn('name="q"', body)
