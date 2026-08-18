from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase

from content.docs_projection import (
    DOCS_PROJECTION_PATH,
    DOCS_ROOT_PATH,
    DOCS_SEARCH_URL,
    DOCS_SOURCE_REVISION,
    _prepare_markdown,
    build_docs_navigation,
    docs_asset_path,
    docs_breadcrumbs,
    docs_navigation_tree,
    docs_page,
    docs_parent,
    docs_projection,
    docs_sequential_navigation,
    render_docs_markdown,
)


class DocsProjectionTests(TestCase):
    def test_projection_is_complete_and_pinned(self) -> None:
        projection = docs_projection()
        self.assertEqual(projection["source"]["revision"], DOCS_SOURCE_REVISION)
        self.assertEqual(projection["root_path"], DOCS_ROOT_PATH)
        self.assertEqual(len(projection["pages"]), 106)
        self.assertEqual(len(projection["assets"]), 39)
        self.assertEqual(
            len({page["public_path"] for page in projection["pages"]}),
            len(projection["pages"]),
        )
        page = docs_page("/docs/courses/ai-dev-tools-zoomcamp/getting-started/")
        if page is None:
            self.fail("getting-started projection page is missing")
        self.assertEqual(page["parent_path"], "/docs/courses/ai-dev-tools-zoomcamp/")

    def test_docs_home_and_detail_emit_source_content_and_canonicals(self) -> None:
        home = self.client.get("/docs/")
        self.assertEqual(home.status_code, 200)
        self.assertNotIn("Location", home.headers)
        self.assertContains(home, '<link rel="canonical" href="https://datatalks.club/docs/">')
        self.assertContains(home, '<meta property="og:url" content="https://datatalks.club/docs/">')
        self.assertContains(home, "DataTalks.Club Zoomcamps Notes and Resources")
        self.assertContains(home, "/docs/courses/")

        detail_path = "/docs/courses/ai-dev-tools-zoomcamp/getting-started/"
        detail = self.client.get(detail_path)
        self.assertEqual(detail.status_code, 200)
        self.assertContains(
            detail,
            f'<link rel="canonical" href="https://datatalks.club{detail_path}">',
        )
        self.assertContains(detail, "For the cross-course onboarding")
        self.assertContains(detail, 'id="getting-started"')
        self.assertContains(detail, 'id="star-the-github-repository"')
        self.assertContains(detail, 'href="/docs/courses/zoomcamp-logistics/joining/"')
        self.assertNotContains(detail, "3f23e006ffdaa498bbc69697408853b6f5eb37dc")
        self.assertContains(detail, DOCS_SEARCH_URL)
        projected_detail = docs_page(detail_path)
        self.assertIsNotNone(projected_detail)
        self.assertContains(detail, (projected_detail or {})["edit_url"])

    def test_tree_is_complete_ordered_and_stable_across_source_reordering(self) -> None:
        pages = docs_projection()["pages"]
        tree = docs_navigation_tree()
        reversed_tree = build_docs_navigation(tuple(reversed(pages)))
        self.assertEqual(tree.root.public_path, DOCS_ROOT_PATH)
        self.assertEqual(len(tree.preorder), 106)
        self.assertEqual(len(tree.documents), 105)
        self.assertEqual(
            [item.public_path for item in tree.preorder],
            [item.public_path for item in reversed_tree.preorder],
        )
        for item in tree.preorder:
            expected = sorted(
                item.children,
                key=lambda child: (
                    int(child.page.get("nav_order") or 0),
                    child.title.casefold(),
                    child.public_path,
                ),
            )
            self.assertEqual(list(item.children), expected)

    def test_tree_validation_fails_closed_with_bounded_source_diagnostics(self) -> None:
        root: dict[str, Any] = {
            "source_path": "index.md",
            "public_path": DOCS_ROOT_PATH,
            "title": "Home",
            "parent_path": None,
        }
        section: dict[str, Any] = {
            "source_path": "section.md",
            "public_path": "/docs/section/",
            "title": "Section",
            "parent_path": None,
        }
        child: dict[str, Any] = {
            "source_path": "child.md",
            "public_path": "/docs/child/",
            "title": "Child",
            "parent_path": "/docs/section/",
        }
        fixtures: dict[str, list[dict[str, Any]]] = {
            "orphan": [root, child | {"parent_path": "/docs/missing/"}],
            "self_parent": [root, child | {"parent_path": "/docs/child/"}],
            "parent_cycle": [
                root,
                section | {"parent_path": "/docs/child/"},
                child | {"parent_path": "/docs/section/"},
            ],
            "duplicate_public_path": [root, section, child | {"public_path": "/docs/section/"}],
            "duplicate_source_path": [root, section, child | {"source_path": "section.md"}],
            "noncanonical_public_path": [root, section | {"public_path": "/docs/section"}],
        }
        for code, fixture in fixtures.items():
            with (
                self.subTest(code=code),
                self.assertRaisesRegex(
                    ImproperlyConfigured,
                    rf"Docs navigation {code}: [^\n]{{1,160}}$",
                ),
            ):
                build_docs_navigation(tuple(fixture))

    def test_landing_contains_every_document_once_in_the_complete_tree(self) -> None:
        response = self.client.get(DOCS_ROOT_PATH)
        self.assertEqual(response.status_code, 200)
        navigation = response.context["docs_navigation"]

        def flatten(items):
            for item in items:
                yield item
                yield from flatten(item.children)

        rendered_items = tuple(flatten(navigation))
        self.assertEqual(len(rendered_items), 105)
        self.assertEqual(
            {item.public_path for item in rendered_items},
            {
                page["public_path"]
                for page in docs_projection()["pages"]
                if page["public_path"] != DOCS_ROOT_PATH
            },
        )
        html = response.content.decode("utf-8")
        tree_html = html.split('<nav class="docs-home-tree"', 1)[1].split("</nav>", 1)[0]
        for item in rendered_items:
            with self.subTest(public_path=item.public_path):
                self.assertEqual(tree_html.count(f'href="{item.public_path}"'), 1)

    def test_root_only_projection_has_explicit_empty_state_without_empty_nav(self) -> None:
        root = deepcopy(docs_page(DOCS_ROOT_PATH) or {})
        tree = build_docs_navigation((root,))
        with (
            patch("content.review_views.projected_docs_page", return_value=root),
            patch("content.review_views.docs_navigation_tree", return_value=tree),
        ):
            response = self.client.get(DOCS_ROOT_PATH)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "DataTalks.Club Zoomcamps Notes and Resources")
        self.assertContains(response, "No documentation sections are available yet.")
        self.assertNotContains(response, 'aria-label="Documentation sections"')

    def test_detail_context_uses_parent_and_depth_first_previous_next(self) -> None:
        tree = docs_navigation_tree()
        first = tree.documents[0]
        middle = tree.documents[len(tree.documents) // 2]
        last = tree.documents[-1]
        for index, item in enumerate((first, middle, last)):
            page = dict(item.page)
            previous, following = docs_sequential_navigation(page)
            with self.subTest(public_path=item.public_path):
                self.assertEqual(
                    previous and previous["public_path"],
                    None
                    if index == 0
                    else tree.documents[tree.documents.index(item) - 1].public_path,
                )
                self.assertEqual(
                    following and following["public_path"],
                    None
                    if item is last
                    else tree.documents[tree.documents.index(item) + 1].public_path,
                )
                response = self.client.get(item.public_path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["docs_parent"], docs_parent(page))
                self.assertEqual(response.context["docs_previous"], previous)
                self.assertEqual(response.context["docs_next"], following)

    def test_detail_marks_current_once_in_each_navigation_landmark(self) -> None:
        path = "/docs/courses/ai-dev-tools-zoomcamp/getting-started/"
        response = self.client.get(path)
        # The design 5a page carries its whole stylesheet inline (issue #179), and
        # that stylesheet names both landmarks in its own comments and selectors,
        # so the landmarks are read from the document below the head.
        html = response.content.decode("utf-8").split("</head>", 1)[1]
        breadcrumb = html.split('aria-label="Breadcrumb"', 1)[1].split("</nav>", 1)[0]
        tree = html.split('aria-label="Documentation sections"', 1)[1].split("</nav>", 1)[0]
        self.assertEqual(breadcrumb.count('aria-current="page"'), 1)
        self.assertEqual(tree.count('aria-current="page"'), 1)
        self.assertIn(f'href="{path}"', tree)

    def test_search_and_edit_actions_are_exact_external_links(self) -> None:
        for path in (DOCS_ROOT_PATH, "/docs/general/guidelines/ai-usage/"):
            page = docs_page(path)
            if page is None:
                self.fail(f"projected Docs page is missing: {path}")
            response = self.client.get(path)
            self.assertContains(response, f'href="{DOCS_SEARCH_URL}"')
            self.assertContains(response, f'href="{page["edit_url"]}"')
            actions = response.content.decode("utf-8").split('class="docs-actions"', 1)[1]
            actions = actions.split("</div>", 1)[0]
            self.assertEqual(actions.count('target="_blank"'), 2)
            self.assertEqual(actions.count('rel="noopener noreferrer"'), 2)
            self.assertNotContains(response, DOCS_SOURCE_REVISION)

    def test_route_alias_unknown_search_and_query_behavior_remain_bounded(self) -> None:
        alias = self.client.get("/docs", query_params={"source": "test"})
        self.assertEqual(alias.status_code, 301)
        self.assertEqual(alias.headers["Location"], "/docs/?source=test")
        self.assertEqual(self.client.get("/docs/search").status_code, 404)
        self.assertEqual(self.client.get("/docs/not-a-projected-page/").status_code, 404)
        queried = self.client.get(DOCS_ROOT_PATH, query_params={"q": "unchanged"})
        self.assertEqual(queried.status_code, 200)
        self.assertContains(queried, '<link rel="canonical" href="https://datatalks.club/docs/">')

    def test_every_projected_page_is_a_trailing_slash_public_page(self) -> None:
        for page in docs_projection()["pages"]:
            public_path = page["public_path"]
            with self.subTest(public_path=public_path):
                response = self.client.get(public_path)
                self.assertEqual(response.status_code, 200)
                self.assertContains(
                    response,
                    f'<link rel="canonical" href="https://datatalks.club{public_path}">',
                )

    def test_navigation_breadcrumbs_follow_source_hierarchy(self) -> None:
        page = docs_page("/docs/general/guidelines/ai-usage/")
        self.assertIsNotNone(page)
        breadcrumbs = docs_breadcrumbs(page or {})
        self.assertEqual(
            [(item["title"], item["public_path"]) for item in breadcrumbs],
            [
                ("Documentation", "/docs/"),
                ("General", "/docs/general/"),
                ("Community Guidelines", "/docs/general/guidelines/"),
            ],
        )

    def test_markdown_renderer_rewrites_liquid_and_sanitizes_html(self) -> None:
        html, headings = render_docs_markdown(
            {
                "body": (
                    "# One heading\n\n"
                    "## One heading\n\n"
                    "[guide]({{ '/courses/ml-zoomcamp/' | relative_url }})\n\n"
                    "<script>alert('no')</script><a href=\"javascript:alert(1)\">bad</a>"
                )
            }
        )
        self.assertEqual([item["id"] for item in headings], ["one-heading", "one-heading-1"])
        self.assertIn('href="/docs/courses/ml-zoomcamp/"', html)
        self.assertNotIn("<script", html.lower())
        self.assertNotIn("javascript:", html.lower())
        self.assertNotRegex(html, re.compile(r"on\w+\s*=", re.IGNORECASE))

    def test_render_time_link_allowlist_preserves_fragments_and_external_links(self) -> None:
        source = (
            "[events](/events.html?utm_source=docs#upcoming) "
            "[events](https://datatalks.club/events.html?utm_source=docs#upcoming) "
            "[empty](/events.html?#) "
            "[podcast](/podcast.html#episodes) "
            "[podcast](https://datatalks.club/podcast.html?season=3#latest) "
            "[books](/books.html?sort=recent) "
            "[books](https://datatalks.club/books.html#archive) "
            "[slack](/slack.html?source=docs#join) "
            "[guidelines](https://datatalks.club/slack/guidelines.html#rules) "
            "[newsletter](/newsletter.html?utm_source=docs#weekly) "
            "[newsletter](https://datatalks.club/newsletter.html#weekly) "
            "[Luma](https://luma.com/dtc-events) "
            "[channel](https://app.slack.com/client/T01ATQK62F8/C0288NJ5XSA)"
        )
        prepared = _prepare_markdown(source)
        self.assertEqual(
            prepared,
            (
                "[events](/events?utm_source=docs#upcoming) "
                "[events](/events?utm_source=docs#upcoming) "
                "[empty](/events?#) "
                "[podcast](/podcast#episodes) "
                "[podcast](/podcast?season=3#latest) "
                "[books](/books?sort=recent) "
                "[books](/books#archive) "
                "[slack](/slack?source=docs#join) "
                "[guidelines](/slack#rules) "
                "newsletter newsletter "
                "[our events page](/events) "
                "[channel](/slack)"
            ),
        )
        self.assertNotIn("newsletter.html", prepared)
        self.assertNotIn("luma.com", prepared)

    def test_community_workspace_slack_links_rewrite_to_the_slack_hub(self) -> None:
        """Workspace deep links become plain ``/slack``; their query and fragment are dropped."""

        source = (
            "[channel](https://app.slack.com/client/T01ATQK62F8/C0288NJ5XSA) "
            "[thread](https://app.slack.com/client/T01ATQK62F8/C0288NJ5XSA/thread_ts=161?x=1#p1) "
            "[workspace root](https://app.slack.com/client/T01ATQK62F8) "
            "[archive]"
            "(https://datatalks-club.slack.com/archives/C06L1RTF10F/p1690?src=docs#anchor) "
            "[any archive path](https://datatalks-club.slack.com/C06L1RTF10F)"
        )
        prepared = _prepare_markdown(source)
        self.assertEqual(
            prepared,
            (
                "[channel](/slack) "
                "[thread](/slack) "
                "[workspace root](/slack) "
                "[archive](/slack) "
                "[any archive path](/slack)"
            ),
        )

    def test_absolute_canonical_slack_links_normalize_with_query_and_fragment(self) -> None:
        prepared = _prepare_markdown(
            "[community](https://datatalks.club/slack) "
            "[tagged](https://datatalks.club/slack?utm_source=docs#join) "
            "[relative stays relative](/slack?utm_source=docs#join)"
        )
        self.assertEqual(
            prepared,
            (
                "[community](/slack) "
                "[tagged](/slack?utm_source=docs#join) "
                "[relative stays relative](/slack?utm_source=docs#join)"
            ),
        )

    def test_non_community_slack_links_stay_byte_for_byte(self) -> None:
        source = (
            "[how to join a channel](https://slack.com/help/articles/205239967-Join-a-channel) "
            "[threads](https://slack.com/help/articles/115000769927-Use-threads-"
            "to-organize-discussions-) "
            "[another workspace](https://app.slack.com/client/T0OTHERTEAM/C0288NJ5XSA) "
            "[another workspace archives]"
            "(https://another-community.slack.com/archives/C06L1RTF10F) "
            "[docs about slack]({{ '/general/slack/' | relative_url }}) "
            "[plain docs link](https://datatalks.club/docs/general/slack/)"
        )
        prepared = _prepare_markdown(source)
        self.assertEqual(
            prepared,
            (
                "[how to join a channel](https://slack.com/help/articles/205239967-Join-a-channel) "
                "[threads](https://slack.com/help/articles/115000769927-Use-threads-"
                "to-organize-discussions-) "
                "[another workspace](https://app.slack.com/client/T0OTHERTEAM/C0288NJ5XSA) "
                "[another workspace archives]"
                "(https://another-community.slack.com/archives/C06L1RTF10F) "
                "[docs about slack](/docs/general/slack/) "
                "[plain docs link](https://datatalks.club/docs/general/slack/)"
            ),
        )

    def test_link_rewrites_do_not_touch_markdown_outside_intended_spans(self) -> None:
        source = (
            "Literal /events.html and https://datatalks.club/slack.html stay unchanged.\n\n"
            "`[events](/events.html)` and `[Luma](https://luma.com/dtc-events)` stay unchanged.\n\n"
            "[external](https://example.com/events.html) and "
            "[other host](https://www.datatalks.club/events.html) stay unchanged.\n\n"
            "Literal https://app.slack.com/client/T01ATQK62F8/C0288NJ5XSA and "
            "https://datatalks-club.slack.com/archives/C06L1RTF10F stay unchanged.\n\n"
            "`[channel](https://app.slack.com/client/T01ATQK62F8/C0288NJ5XSA)` stays unchanged.\n\n"
            "```\n[channel](https://app.slack.com/client/T01ATQK62F8/C0288NJ5XSA)\n```"
        )
        self.assertEqual(_prepare_markdown(source), source)

    def test_rendered_activity_pages_use_canonical_hubs_and_targeted_events_cta(self) -> None:
        expected_hubs = {
            "/docs/activities/": ("/events",),
            "/docs/activities/podcast/": ("/podcast", "/events"),
            "/docs/activities/webinars/": ("/events",),
            "/docs/activities/workshops/": ("/events",),
            "/docs/activities/book-of-the-week/": ("/books", "/slack"),
        }
        for public_path, destinations in expected_hubs.items():
            with self.subTest(public_path=public_path):
                page = docs_page(public_path)
                self.assertIsNotNone(page)
                rendered, _ = render_docs_markdown(page or {})
                for destination in destinations:
                    self.assertIn(f'href="{destination}', rendered)
                self.assertNotRegex(
                    rendered,
                    r'href="(?:https://datatalks\.club)?/(?:events|podcast|books)\.html',
                )
        activities, _ = render_docs_markdown(docs_page("/docs/activities/") or {})
        self.assertIn('href="/events"', activities)
        self.assertNotIn("luma.com", activities.lower())
        self.assertNotIn(">Luma<", activities)
        workshops, _ = render_docs_markdown(docs_page("/docs/activities/workshops/") or {})
        self.assertIn("via Luma", workshops)

    def test_all_affected_docs_pages_apply_slack_and_newsletter_exceptions(self) -> None:
        newsletter_pages = {
            "/docs/courses/llm-zoomcamp/resources/": "DataTalks.Club newsletter",
            "/docs/courses/ml-zoomcamp/resources/": "DataTalks.Club Newsletter",
            "/docs/courses/zoomcamp-logistics/email/": "DataTalks.Club newsletter",
        }
        for public_path, heading_copy in newsletter_pages.items():
            with self.subTest(public_path=public_path):
                page = docs_page(public_path)
                self.assertIsNotNone(page)
                rendered, _ = render_docs_markdown(page or {})
                self.assertIn(heading_copy, rendered)
                self.assertNotRegex(rendered, r'href="[^" ]*newsletter\.html')
        ml_resources, _ = render_docs_markdown(
            docs_page("/docs/courses/ml-zoomcamp/resources/") or {}
        )
        self.assertIn('href="https://us19.campaign-archive.com/home/', ml_resources)

        for page in docs_projection()["pages"]:
            if not re.search(r"/slack(?:/guidelines)?\.html", str(page["body"])):
                continue
            with self.subTest(public_path=page["public_path"]):
                rendered, _ = render_docs_markdown(page)
                self.assertNotRegex(
                    rendered,
                    r'href="(?:https://datatalks\.club)?/slack(?:/guidelines)?\.html',
                )
                self.assertIn('href="/slack', rendered)

    def test_every_workspace_link_on_the_sixteen_affected_pages_renders_as_slack(self) -> None:
        """The 32 distinct workspace URLs (72 occurrences) all land on the canonical hub."""

        workspace_destination = re.compile(
            r"\]\(\s*(?:<)?https://(?:app\.slack\.com|datatalks-club\.slack\.com)/"
        )
        affected = 0
        for page in docs_projection()["pages"]:
            rendered, _ = render_docs_markdown(page)
            with self.subTest(public_path=page["public_path"]):
                self.assertNotRegex(rendered, r'href="[^"]*app\.slack\.com')
                self.assertNotRegex(rendered, r'href="[^"]*datatalks-club\.slack\.com')
            if workspace_destination.search(str(page["body"])):
                affected += 1
                with self.subTest(affected=page["public_path"]):
                    self.assertIn('href="/slack"', rendered)
        self.assertEqual(affected, 16)

    def test_absolute_canonical_slack_destinations_render_root_relative(self) -> None:
        canonical_absolute = re.compile(r"\]\(\s*(?:<)?https://datatalks\.club/slack(?![\w./-])")
        affected = [
            page
            for page in docs_projection()["pages"]
            if canonical_absolute.search(str(page["body"]))
        ]
        self.assertEqual(len(affected), 6)
        for page in affected:
            with self.subTest(public_path=page["public_path"]):
                rendered, _ = render_docs_markdown(page)
                self.assertNotRegex(rendered, r'href="https://(?:www\.)?datatalks\.club/slack[?"]')
                self.assertIn('href="/slack"', rendered)

    def test_slack_product_documentation_citations_stay_external(self) -> None:
        help_destination = re.compile(r"\]\(\s*(?:<)?(https://slack\.com/help/[^\s)>]+)")
        citations: dict[str, set[str]] = {}
        for page in docs_projection()["pages"]:
            for match in help_destination.finditer(str(page["body"])):
                citations.setdefault(page["public_path"], set()).add(match.group(1))
        self.assertEqual(len(citations), 2)
        for public_path, urls in citations.items():
            with self.subTest(public_path=public_path):
                rendered, _ = render_docs_markdown(docs_page(public_path) or {})
                for url in urls:
                    self.assertIn(f'href="{url}"', rendered)

    def test_rendering_keeps_projection_source_and_metadata_immutable(self) -> None:
        before_bytes = DOCS_PROJECTION_PATH.read_bytes()
        before_projection = json.loads(before_bytes)
        before_digest = hashlib.sha256(before_bytes).hexdigest()

        for page in docs_projection()["pages"]:
            render_docs_markdown(page)

        after_bytes = DOCS_PROJECTION_PATH.read_bytes()
        self.assertEqual(hashlib.sha256(after_bytes).hexdigest(), before_digest)
        self.assertEqual(json.loads(after_bytes), before_projection)

    def test_referenced_assets_are_served_only_from_the_pinned_allowlist(self) -> None:
        asset = docs_projection()["assets"][0]
        public_path = asset["public_path"]
        response = self.client.get(public_path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], asset["content_type"])
        payload = b"".join(response.streaming_content)  # type: ignore[attr-defined]
        self.assertEqual(len(payload), asset["size"])
        self.assertEqual(response.headers["Cache-Control"], "public, max-age=86400")
        resolved = docs_asset_path(public_path.removeprefix("/docs/assets/"))
        if resolved is None:
            self.fail("projected asset did not resolve")
        self.assertEqual(resolved[1], asset["content_type"])

        for path in (
            "/docs/assets/images/not-referenced.png",
            "/docs/assets/../docs_projection.json",
            "/docs/assets/images/brand-assets/../../../../content/docs_projection.json",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)
