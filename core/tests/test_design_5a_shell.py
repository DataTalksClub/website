"""One shell for every design 5a page (issue #179).

The pages rebuilt on design 5a each inline their own document rather than
extending a base template, and for a while each also inlined its own copy of the
masthead, the footer and the script block.  Copying a shell five times is how
`/slack` left the primary navigation and `user_menu.js` left the script set
without a single test going red: every page still passed its own contracts, and
nothing compared the pages to each other.

These tests do that comparison.  The shell now lives in
``core/_site_shell_head.html`` and ``core/_site_shell_foot.html``; a page that
forks it, drops an entry from it, or forgets to include it fails here.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

from django.contrib.auth import get_user_model
from django.template.loader import get_template
from django.templatetags.static import static
from django.test import TestCase
from django.urls import reverse

from accounts.navigation import login_url_for_path
from content.public_data import public_projection
from courses.models.course import Course

# Every page built in the design 5a system, and the navigation entry each one is.
# A new page in the system belongs in this list.
DESIGN_5A_TEMPLATES = (
    "core/home.html",
    "courses/course_list.html",
    "courses/course.html",
    "public/events.html",
    "public/event_detail.html",
    "public/podcast_hub.html",
    "public/podcast_detail.html",
    "public/wiki_hub.html",
    "public/wiki_search.html",
    "public/wiki_graph.html",
    "public/wiki_special.html",
    "public/wiki_detail.html",
    # One template behind two navigation entries: the blog hub and the books hub.
    "public/collection_hub.html",
    "public/person_detail.html",
    "public/article_detail.html",
    # One document behind every page that is a heading and some prose: the three
    # legal pages (/terms, /privacy, /impressum), /slack, and the page a lost
    # visitor lands on.  They are the same shape, so the shell they carry lives
    # in the text page they share.  (The error page is in the system too — a lost
    # visitor still needs the navigation — but it is not in `page_paths` below,
    # which asserts a 200.)
    "public/text_page.html",
    # The documentation index and one documentation page; the section tree they
    # share is the source-backed navigation from issue #176.
    "review/docs_home.html",
    "review/docs_detail.html",
    # The FAQ hub and one course FAQ; the hub indexes the courses and the course
    # page is the long question-and-answer document behind it.
    "review/faq_home.html",
    "review/faq_detail.html",
    # The enrolled learner's own pages, below the course page: the course
    # dashboard, the per-course enrollment profile, the registration campaign
    # landing page, the year in review and one person's shareable copy of it,
    # and the courses landing greeting.  They are learner surfaces rather than
    # public indexes, so they are not in `page_paths` below (each needs its own
    # signed-in or seeded fixture), but they carry the same shell.
    "courses/dashboard.html",
    "courses/enrollment.html",
    "courses/register.html",
    "courses/wrapped.html",
    "courses/user_wrapped.html",
    "index.html",
    # The scoring and submission surfaces below a course: the leaderboard, one
    # student's score breakdown, the form that flags a leaderboard record, one
    # homework, its public statistics and its submissions list.  Learner
    # surfaces again, so they are not in `page_paths` below.
    "courses/leaderboard.html",
    "courses/leaderboard_score_breakdown.html",
    "courses/leaderboard_complaint.html",
    "homework/homework.html",
    "homework/stats.html",
    "homework/submissions.html",
    # The account entrance family: the pages a visitor meets between deciding to
    # join and being signed in.  Sign in, sign out and the provider outcomes were
    # rebuilt with the rest of the system; sign up, its closed state, the four
    # password-reset steps and the inactive-account notice joined afterwards.
    # They are signed-out surfaces with their own fixtures, so they are not in
    # `page_paths` below, but the shell they carry has to be the same one.
    "accounts/login.html",
    "account/logout.html",
    "account/signup.html",
    "account/signup_closed.html",
    "account/account_inactive.html",
    "account/password_reset.html",
    "account/password_reset_done.html",
    "account/password_reset_from_key.html",
    "account/password_reset_from_key_done.html",
    "socialaccount/signup.html",
)
SHELL_PARTIALS = ("core/_site_shell_head.html", "core/_site_shell_foot.html")

# The primary navigation, in the order the site has always offered it.  Slack is
# the community's main gathering place and stays in the row on every page.
# A signed-out visitor gets one more entry after these nine: the one-line phone
# masthead (issue #179) folds "Log in" into the panel below 40rem, so the shell
# renders it there for every anonymous request.
EXPECTED_NAVIGATION = (
    ("Events", "events"),
    ("Courses", "course_list"),
    ("Blog", "articles"),
    ("Podcast", "podcast"),
    ("Wiki", "wiki-home"),
    ("Books", "books"),
    ("Docs", "docs-home"),
    ("FAQ", "faq-home"),
    ("Slack", "slack"),
)

# The scripts every page in the system runs, in the order the shell loads them.
EXPECTED_SCRIPTS = (
    "timezone_preference.js",
    "user_menu.js",
    "core/site_navigation.js",
    "core/accessibility.js",
    "core/analytics_preferences.js",
)


class TemplateOrigin(Protocol):
    name: str


class ResolvedTemplate(Protocol):
    origin: TemplateOrigin | None


NAVIGATION_BLOCK = re.compile(r'<div id="site-navigation-links".*?>(.*?)</div>', re.S)
NAVIGATION_LINK = re.compile(r"<a\b(?P<attributes>[^>]*)>(?P<label>.*?)</a>", re.S)
SCRIPT_SOURCE = re.compile(r'<script src="([^"]+)"')


def navigation_entries(body: str) -> list[tuple[str, str, bool]]:
    """Return (label, href, is-current) for each primary navigation link."""

    block = NAVIGATION_BLOCK.search(body)
    assert block is not None, "the page has no #site-navigation-links row"
    entries = []
    for link in NAVIGATION_LINK.finditer(block.group(1)):
        attributes = link.group("attributes")
        href = re.search(r'href="([^"]+)"', attributes)
        assert href is not None, attributes
        entries.append(
            (
                " ".join(link.group("label").split()),
                href.group(1),
                'aria-current="page"' in attributes,
            )
        )
    return entries


class DesignFiveAShellTests(TestCase):
    """Render every page in the system and compare their shells to each other."""

    course: Course
    episode: dict[str, Any]
    wiki_page: dict[str, Any]
    person: dict[str, Any]
    article: dict[str, Any]
    event: dict[str, Any]

    @classmethod
    def setUpTestData(cls) -> None:
        cls.course = Course.objects.create(
            title="Shell parity course",
            slug="shell-parity-course",
            description="Fixture for the design 5a shell comparison.",
            visible=True,
        )
        cls.episode = next(iter(public_projection()["podcasts_by_slug"].values()))
        cls.wiki_page = public_projection()["wiki"][0]
        cls.person = public_projection()["people_by_slug"]["alexeygrigorev"]
        cls.article = public_projection()["articles"][0]
        cls.event = public_projection()["events"][0]

    def page_paths(self) -> dict[str, str]:
        return {
            "home": reverse("home"),
            "courses index": reverse("course_list"),
            "course page": reverse("course", kwargs={"course_slug": self.course.slug}),
            "events index": reverse("events"),
            "past events": reverse("events-past"),
            "event page": self.event["public_path"],
            "podcast index": reverse("podcast"),
            "podcast episode": self.episode["public_path"],
            "wiki hub": reverse("wiki-home"),
            "wiki search": f"{reverse('wiki-home')}?q=machine+learning",
            "wiki graph": reverse("wiki-graph"),
            "wiki special pages": reverse("wiki-special"),
            "wiki page": self.wiki_page["public_path"],
            # Both collections the shared collection hub serves, because a page
            # that branches on its collection can lose the shell in one branch.
            "blog index": reverse("articles"),
            "books index": reverse("books"),
            "person profile": self.person["public_path"],
            "blog article": self.article["public_path"],
            # The documentation index and one page below it, because the docs
            # pages are two templates sharing one section tree.
            "docs index": reverse("docs-home"),
            "docs page": "/docs/courses/ai-dev-tools-zoomcamp/getting-started/",
            # The FAQ hub and the smallest course FAQ below it, because the two
            # are separate templates rather than one page in two states.
            "faq index": reverse("faq-home"),
            "faq course": reverse("faq-course", kwargs={"course_slug": "ai-dev-tools-zoomcamp"}),
            # All three legal documents, because they share one base and a page
            # that overrides a block can still lose the shell in one branch.
            "terms": reverse("terms"),
            "privacy": reverse("privacy"),
            "impressum": reverse("impressum"),
            # The page that lost the Slack link when the shell was copied.
            "slack": reverse("slack"),
        }

    def rendered_pages(self) -> dict[str, str]:
        bodies = {}
        for name, path in self.page_paths().items():
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, name)
            for partial in SHELL_PARTIALS:
                self.assertTemplateUsed(response, partial, msg_prefix=name)
            bodies[name] = response.content.decode()
        return bodies

    def test_every_design_5a_page_offers_the_same_navigation_entries(self) -> None:
        destinations = [(label, reverse(route)) for label, route in EXPECTED_NAVIGATION]
        paths = self.page_paths()

        for name, body in self.rendered_pages().items():
            with self.subTest(page=name):
                entries = navigation_entries(body)
                # Signed out, the panel closes with "Log in" (its phone-width home
                # since the one-line masthead), pointing back at the page it is on.
                # The shell derives next from request.path, so a query string (the
                # wiki search page) is not part of the return destination.
                login = login_url_for_path(urlsplit(paths[name]).path)
                expected = [*destinations, ("Log in", login)]
                self.assertEqual([(label, href) for label, href, _ in entries], expected)

    def test_signed_in_panels_offer_the_nine_destinations_and_no_login(self) -> None:
        """The account menu owns auth once signed in; the panel goes back to nine."""

        user = get_user_model().objects.create_user(
            username="shell-reader@example.invalid",
            email="shell-reader@example.invalid",
        )
        self.client.force_login(user)
        expected = [(label, reverse(route)) for label, route in EXPECTED_NAVIGATION]

        for name, body in self.rendered_pages().items():
            with self.subTest(page=name):
                entries = navigation_entries(body)
                self.assertEqual([(label, href) for label, href, _ in entries], expected)

    def test_every_design_5a_page_reaches_the_community_slack(self) -> None:
        """The regression that started this: four pages had no Slack link at all."""

        for name, body in self.rendered_pages().items():
            with self.subTest(page=name):
                slack = [href for label, href, _ in navigation_entries(body) if label == "Slack"]
                self.assertEqual(slack, [reverse("slack")])

    def test_every_design_5a_page_marks_only_the_entry_it_is(self) -> None:
        expected_current = {
            "home": None,
            "courses index": "Courses",
            "course page": "Courses",
            "events index": "Events",
            "past events": "Events",
            "event page": "Events",
            "podcast index": "Podcast",
            "podcast episode": "Podcast",
            "wiki hub": "Wiki",
            "wiki search": "Wiki",
            "wiki graph": "Wiki",
            "wiki special pages": "Wiki",
            "wiki page": "Wiki",
            "blog index": "Blog",
            "books index": "Books",
            # A profile sits under no navigation entry: the site offers no people
            # index, so the row marks nothing while the page is open.
            "person profile": None,
            "blog article": "Blog",
            "docs index": "Docs",
            "docs page": "Docs",
            "faq index": "FAQ",
            "faq course": "FAQ",
            # The legal documents sit under no navigation entry; the footer's own
            # Legal navigation is how a reader reaches them.
            "terms": None,
            "privacy": None,
            "impressum": None,
            "slack": "Slack",
        }

        for name, body in self.rendered_pages().items():
            with self.subTest(page=name):
                entries = navigation_entries(body)
                current = [label for label, _href, is_current in entries if is_current]
                self.assertEqual(
                    current,
                    [] if expected_current[name] is None else [expected_current[name]],
                )

    def test_every_design_5a_page_loads_the_same_script_set(self) -> None:
        """user_menu.js closes the account menu, and left every page with the rebuild."""

        expected = [static(source) for source in EXPECTED_SCRIPTS]

        for name, body in self.rendered_pages().items():
            with self.subTest(page=name):
                sources = SCRIPT_SOURCE.findall(body)
                # A page may append its own scripts, never reorder or drop the shared set.
                self.assertEqual(sources[: len(expected)], expected)

    def test_no_design_5a_page_keeps_its_own_copy_of_the_shell(self) -> None:
        """The shell is included, never inlined: that is what stops the next drift."""

        for name in DESIGN_5A_TEMPLATES:
            with self.subTest(template=name):
                origin = cast(ResolvedTemplate, get_template(name)).origin
                if origin is None:
                    self.fail(f"template has no loader origin: {name}")
                source = Path(origin.name).read_text(encoding="utf-8")
                for partial in SHELL_PARTIALS:
                    self.assertIn(f'{{% include "{partial}" %}}', source)
                self.assertNotIn('<header class="masthead">', source)
                self.assertNotIn("analytics_preferences.js", source)
