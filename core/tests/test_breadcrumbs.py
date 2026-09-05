"""The one breadcrumb trail: its markup contract, and that nothing writes its own."""

from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings
from django.template import Context, Template, TemplateSyntaxError
from django.test import SimpleTestCase

from core.breadcrumbs import Crumb, Trail, trail

# Templates that still draw breadcrumb markup by hand, each for a reason that is
# not "nobody got to it".  Anything else has to go through {% breadcrumbs %}.
HAND_WRITTEN_TRAILS = {
    # The vendored copy of the adopted platform's shell, kept byte-comparable to
    # the upstream file by core/tests/test_course_platform_vendor_assets.py.  No
    # template extends it, so it renders nothing.
    "course_platform_templates/base.html": "vendored adopted shell, not rendered",
}

_TRAIL_MARKUP = re.compile(r"<nav[^>]*\bbreadcrumbs\b[^>]*>")


def render(source: str, **context: object) -> str:
    return Template("{% load breadcrumbs %}" + source).render(Context(context))


class BreadcrumbMarkupTests(SimpleTestCase):
    def test_inline_levels_render_one_linked_crumb_each(self) -> None:
        markup = render(
            '{% breadcrumbs "Courses" "/courses/" "Machine Learning Zoomcamp" "/courses/ml/" %}'
        )

        self.assertEqual(
            markup,
            '<nav class="breadcrumbs" aria-label="Breadcrumb"><ol>'
            '<li><a href="/courses/">Courses</a></li>'
            '<li><a href="/courses/ml/">Machine Learning Zoomcamp</a></li>'
            "</ol></nav>",
        )

    def test_the_current_page_is_marked_once_and_is_never_a_link(self) -> None:
        markup = render('{% breadcrumbs "Courses" "/courses/" current="registration" %}')

        self.assertEqual(markup.count('aria-current="page"'), 1)
        self.assertIn('<li aria-current="page">registration</li>', markup)
        self.assertNotIn('<a href="">', markup)

    def test_a_trail_without_a_current_page_stops_at_the_last_ancestor(self) -> None:
        """The default: the h1 beneath the trail says where the reader is."""

        markup = render('{% breadcrumbs "Courses" "/courses/" %}')

        self.assertNotIn("aria-current", markup)
        self.assertEqual(markup.count("<li"), 1)

    def test_a_level_with_no_label_is_dropped_rather_than_drawn_empty(self) -> None:
        """An ancestor that only sometimes exists costs the caller no {% if %}."""

        markup = render(
            '{% breadcrumbs "Courses" "/courses/" missing_title missing_url'
            ' current="registration" %}'
        )

        self.assertEqual(markup.count("<li"), 2)
        self.assertIn(">Courses</a>", markup)

    def test_a_level_with_no_destination_is_named_without_being_linked(self) -> None:
        markup = render('{% breadcrumbs "Studio" "" "Courses" "/studio/courses/" %}')

        self.assertIn("<li><span>Studio</span></li>", markup)
        self.assertNotIn('href=""', markup)

    def test_the_separator_is_never_written_into_the_markup(self) -> None:
        """It is CSS-drawn with empty alternative text, so it is never announced."""

        markup = render('{% breadcrumbs "Courses" "/courses/" current="registration" %}')

        self.assertNotIn("/<", markup)
        self.assertNotIn("&#x2F;", markup)
        self.assertNotIn(">/</", markup)

    def test_the_primitive_class_is_always_present_and_page_classes_lead(self) -> None:
        markup = render('{% breadcrumbs "Wiki" "/wiki" nav_class="shell shell-reading" %}')

        self.assertIn(
            '<nav class="shell shell-reading breadcrumbs" aria-label="Breadcrumb">', markup
        )

    def test_labels_and_destinations_are_escaped(self) -> None:
        markup = render(
            "{% breadcrumbs label url current=label %}",
            label="Q&A <script>",
            url="/a?b=1&c=2",
        )

        self.assertNotIn("<script>", markup)
        self.assertIn("Q&amp;A", markup)
        self.assertIn("/a?b=1&amp;c=2", markup)

    def test_a_label_without_a_destination_after_it_is_a_template_error(self) -> None:
        with self.assertRaises(TemplateSyntaxError):
            render('{% breadcrumbs "Courses" "/courses/" "Orphan" %}')


class PublishedTrailTests(SimpleTestCase):
    def test_a_trail_draws_its_own_ancestors_and_marks_itself(self) -> None:
        markup = render(
            "{% breadcrumbs breadcrumbs %}",
            breadcrumbs=trail(("Blog", "/blog"), ("A title", "/blog/a.html")),
        )

        self.assertIn('<li><a href="/blog">Blog</a></li>', markup)
        self.assertIn('<li aria-current="page">A title</li>', markup)

    def test_the_published_list_is_the_drawn_trail_plus_the_site_root(self) -> None:
        """One value, two renderings: the visible crumbs cannot drift from the JSON-LD."""

        published = trail(("Blog", "/blog"), ("A title", "/blog/a.html")).published_items()

        self.assertEqual(
            published,
            (("Home", "/"), ("Blog", "/blog"), ("A title", "/blog/a.html")),
        )

    def test_a_page_directly_under_the_site_root_has_no_ancestor(self) -> None:
        person = trail(("Alexey", "/people/alexey.html"))

        self.assertEqual(person.ancestors, ())
        self.assertEqual(person.current, Crumb("Alexey", "/people/alexey.html"))
        self.assertEqual(
            person.published_items(),
            (("Home", "/"), ("Alexey", "/people/alexey.html")),
        )

    def test_a_trail_names_at_least_the_page_itself(self) -> None:
        with self.assertRaises(ValueError):
            trail()

    def test_an_empty_trail_publishes_only_the_site_root(self) -> None:
        self.assertEqual(Trail().published_items(), (("Home", "/"),))


class OneTrailEverywhereTests(SimpleTestCase):
    """No page writes breadcrumb markup: they all name levels and the tag draws them."""

    def test_only_the_shared_partial_carries_breadcrumb_nav_markup(self) -> None:
        root = Path(settings.BASE_DIR)
        offenders: list[str] = []
        for location in (
            "course_platform_templates",
            "templates",
            "accounts/templates",
            "core/templates",
            "courses/templates",
            "studio_courses/templates",
        ):
            for path in sorted((root / location).rglob("*.html")):
                relative = path.relative_to(root).as_posix()
                if relative == "templates/core/_breadcrumbs.html":
                    continue
                if relative in HAND_WRITTEN_TRAILS:
                    continue
                if _TRAIL_MARKUP.search(path.read_text(encoding="utf-8")):
                    offenders.append(relative)

        self.assertEqual(offenders, [])

    def test_every_hand_written_exception_still_exists_and_states_a_reason(self) -> None:
        root = Path(settings.BASE_DIR)
        for relative, reason in HAND_WRITTEN_TRAILS.items():
            with self.subTest(template=relative):
                self.assertTrue((root / relative).is_file())
                self.assertTrue(reason)


class BreadcrumbsWithoutATrailTests(SimpleTestCase):
    """A template rendered without its trail draws nothing, and does not raise.

    The accessibility fixture routes render real page templates with a stripped
    context, so ``{% breadcrumbs breadcrumbs %}`` resolves its one argument to
    the empty string.  That used to raise TemplateSyntaxError and 500 the page.
    """

    def test_a_missing_trail_variable_renders_nothing(self):
        rendered = Template("{% load breadcrumbs %}{% breadcrumbs breadcrumbs %}").render(
            Context({})
        )

        self.assertEqual(rendered, "")

    def test_an_empty_trail_object_renders_nothing(self):
        rendered = Template("{% load breadcrumbs %}{% breadcrumbs trail %}").render(
            Context({"trail": Trail()})
        )

        self.assertEqual(rendered, "")

    def test_a_lone_empty_level_does_not_swallow_the_levels_beside_it(self):
        rendered = Template(
            "{% load breadcrumbs %}{% breadcrumbs missing 'Courses' '/courses' current='Now' %}"
        ).render(Context({}))

        self.assertIn('<a href="/courses">Courses</a>', rendered)
        self.assertIn('<li aria-current="page">Now</li>', rendered)
