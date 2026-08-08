from __future__ import annotations

import re
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from content.models import (
    ContentAsset,
    ContentDocument,
    ContentRelation,
    ContentRelease,
    ContentSource,
)
from content.review_projection import (
    REQUIRED_SOURCE_REVISIONS,
    event_groups,
    projected_events,
    record_provenance,
    review_projection,
)
from courses.models.course import Course, CourseRegistration, Enrollment
from jobs.models import DurableJob


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self.actions: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "a" and values.get("href"):
            self.links.append(values["href"] or "")
        if tag == "form" and values.get("action"):
            self.actions.append(values["action"] or "")


ROUTES = (
    ("/", "Welcome to DataTalks.Club", "https://datatalks.club/"),
    ("/events.html", "Events", "https://datatalks.club/events.html"),
    ("/articles.html", "Articles", "https://datatalks.club/articles.html"),
    (
        "/blog/ai-dev-tools-zoomcamp.html",
        "AI Dev Tools Zoomcamp 2026",
        "https://datatalks.club/blog/ai-dev-tools-zoomcamp.html",
    ),
    ("/podcast.html", "DataTalks.Club Podcast", "https://datatalks.club/podcast.html"),
    (
        "/podcast/s24e06-how-to-build-ai-that-actually-ships-in-production.html",
        "How to Build AI That Actually Ships in Production",
        "https://datatalks.club/podcast/s24e06-how-to-build-ai-that-actually-ships-in-production.html",
    ),
    (
        "/people/aleksandrkim.html",
        "Aleksandr Kim",
        "https://datatalks.club/people/aleksandrkim.html",
    ),
    ("/books.html", "Books", "https://datatalks.club/books.html"),
    (
        "/books/20250922-how-software-fails.html",
        "How Software Fails",
        "https://datatalks.club/books/20250922-how-software-fails.html",
    ),
    ("/docs/", "Documentation", "https://datatalks.club/docs/"),
    (
        "/docs/courses/ai-dev-tools-zoomcamp/getting-started/",
        "Getting Started",
        "https://datatalks.club/docs/courses/ai-dev-tools-zoomcamp/getting-started/",
    ),
    ("/faq/", "Frequently Asked Questions", "https://datatalks.club/faq/"),
    (
        "/faq/ai-dev-tools-zoomcamp.html",
        "AI Dev Tools Zoomcamp FAQ",
        "https://datatalks.club/faq/ai-dev-tools-zoomcamp.html",
    ),
    ("/podwiki/", "Podcast Wiki", "https://datatalks.club/podwiki/"),
    (
        "/podwiki/wiki/ai-coding-tools/",
        "AI Coding Tools",
        "https://datatalks.club/podwiki/wiki/ai-coding-tools/",
    ),
    (
        "/podwiki/search/?q=no-such-review-topic",
        "No matches for",
        "https://datatalks.club/podwiki/search/",
    ),
    ("/slack.html", "Join our Slack", "https://datatalks.club/slack.html"),
    ("/courses/", "AI Dev Tools Zoomcamp", "https://datatalks.club/courses/"),
    (
        "/courses/ai-dev-tools-zoomcamp/",
        "AI Dev Tools Zoomcamp",
        "https://datatalks.club/courses/ai-dev-tools-zoomcamp/",
    ),
    (
        "/courses/ai-dev-tools-zoomcamp/cohorts/ai-dev-tools-2026/",
        "AI Dev Tools Zoomcamp 2026",
        "https://datatalks.club/courses/ai-dev-tools-zoomcamp/cohorts/ai-dev-tools-2026/",
    ),
    (
        "/courses/ai-dev-tools-zoomcamp/cohorts/ai-dev-tools-2026/registration-preview/",
        "Registration is not enabled",
        "https://datatalks.club/courses/ai-dev-tools-zoomcamp/cohorts/ai-dev-tools-2026/registration-preview/",
    ),
)


class ReviewSkeletonRouteTests(TestCase):
    def test_required_routes_render_with_exact_canonical_and_noindex(self) -> None:
        for path, marker, canonical in ROUTES:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.request["PATH_INFO"], urlsplit(path).path)
                self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
                self.assertContains(response, marker)
                self.assertContains(
                    response,
                    f'<link rel="canonical" href="{canonical}">',
                    count=1,
                )

    def test_public_content_routes_support_head_except_get_only_preview(self) -> None:
        preview_path = "".join(
            (
                "/courses/ai-dev-tools-zoomcamp/cohorts/ai-dev-tools-2026/",
                "registration-preview/",
            )
        )
        for path, _marker, _canonical in ROUTES:
            with self.subTest(path=path):
                response = self.client.head(path)
                if urlsplit(path).path == preview_path:
                    self.assertEqual(response.status_code, 405)
                    self.assertEqual(response.headers["Allow"], "GET")
                    continue
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")

    def test_every_page_link_is_local_or_an_allowed_third_party(self) -> None:
        allowed_external_hosts = {
            "airtable.com",
            "alexkimds.github.io",
            "amzn.eu",
            "github.com",
            "leanpub.com",
            "linkedin.com",
            "luma.com",
            "open.spotify.com",
            "podcasts.apple.com",
            "t.me",
            "www.linkedin.com",
            "www.youtube.com",
        }
        for path, _marker, _canonical in ROUTES:
            with self.subTest(path=path):
                response = self.client.get(path)
                parser = LinkParser()
                parser.feed(response.content.decode())
                for destination in parser.links + parser.actions:
                    parsed = urlsplit(destination)
                    if parsed.scheme or parsed.netloc:
                        self.assertIn(parsed.scheme, {"http", "https"})
                        self.assertIn(parsed.netloc, allowed_external_hosts)
                        continue
                    linked_response = self.client.get(destination)
                    self.assertNotEqual(
                        linked_response.status_code,
                        404,
                        f"{path} links to missing {destination}",
                    )

    def test_projection_has_exact_pinned_provenance_and_one_person(self) -> None:
        projection = review_projection()
        self.assertEqual(
            {key: value["revision"] for key, value in projection["sources"].items()},
            REQUIRED_SOURCE_REVISIONS,
        )
        self.assertEqual(projection["podcast"]["guest"], "aleksandrkim")
        self.assertEqual(
            [event["speaker"] for event in projection["events"]].count("aleksandrkim"),
            1,
        )
        self.assertEqual(
            record_provenance(projection["people"]["aleksandrkim"])["source_path"],
            "_people/aleksandrkim.md",
        )
        self.assertEqual(
            record_provenance(projection["course"]["platform_provenance"]),
            {
                "repository": "DataTalksClub/course-management-platform",
                "revision": REQUIRED_SOURCE_REVISIONS["courses"],
                "source_path": "courses/models/course.py",
            },
        )
        expected_source_paths = {
            "article": "_posts/2025-09-23-ai-dev-tools-zoomcamp.md",
            "book": "_books/20250922-how-software-fails.md",
            "course": "_posts/2025-09-23-ai-dev-tools-zoomcamp.md",
            "docs": "courses/ai-dev-tools-zoomcamp/getting-started.md",
            "faq": (
                "_questions/ai-dev-tools-zoomcamp/general/"
                "001_4487db3924_how-do-i-access-the-course-modules-and-materials.md"
            ),
            "podcast": ("_podcast/s24e06-how-to-build-ai-that-actually-ships-in-production.md"),
            "podwiki": "_wiki/ai-coding-tools.md",
            "slack": "slack.md",
        }
        self.assertEqual(
            {
                key: record_provenance(projection[key])["source_path"]
                for key in expected_source_paths
            },
            expected_source_paths,
        )
        self.assertEqual(
            {event["source_path"] for event in projection["events"]},
            {"_data/events.yaml"},
        )

    def test_event_status_is_timezone_aware_and_truthful(self) -> None:
        before = event_groups(datetime.fromisoformat("2026-08-08T12:00:00+02:00"))
        after = event_groups(datetime.fromisoformat("2026-09-01T12:00:00+02:00"))

        self.assertTrue(all(event["starts_at"].tzinfo for event in before.upcoming))
        self.assertEqual(
            before.upcoming[0]["title"],
            "Test, Containerize, and Deploy an AI-Assisted App",
        )
        self.assertFalse(after.upcoming)
        self.assertEqual(after.recent[0]["title"], "AI Dev Tools Zoomcamp 2026 Course Launch")
        self.assertEqual(before.upcoming[0]["display_time"], "Aug 10, 2026, 14:00 CEST")
        self.assertEqual(
            {event["speaker_name"] for event in before.upcoming},
            {"Alexey Grigorev", "Nicholas Lotz"},
        )

    @patch("content.review_projection.timezone.now")
    def test_events_render_an_honest_no_upcoming_state(self, now: MagicMock) -> None:
        now.return_value = datetime.fromisoformat("2026-09-01T12:00:00+02:00")

        response = self.client.get("/events.html")

        self.assertContains(
            response,
            "No upcoming events are listed in the current source snapshot.",
        )
        self.assertContains(response, "AI Dev Tools Zoomcamp 2026 Course Launch")

    def test_person_event_relationship_does_not_depend_on_current_date(self) -> None:
        recorded_event = next(
            event for event in projected_events() if event["speaker"] == "aleksandrkim"
        )

        self.assertEqual(
            recorded_event["public_path"],
            "/events.html#2026-06-15-how-to-build-ai-that-actually-ships-in-production",
        )
        self.assertEqual(self.client.get("/people/aleksandrkim.html").status_code, 200)

    def test_projected_copy_is_source_faithful(self) -> None:
        projection = review_projection()

        self.assertEqual(
            projection["podcast"]["transcript"][0]["text"],
            "Today we are going to talk about AI engineering and what it takes to build "
            "AI in production along with all the glamorous work behind this. We have a "
            "special guest today named Aleksandr Kim. Aleksandr is a senior data scientist "
            "at Intuit. He is based in London doing what people usually describe as AI "
            "engineering. This is an overloaded term.",
        )
        self.assertEqual(
            projection["article"]["sections"][0]["body"],
            "AI Dev Tools Zoomcamp is for developers and technical data professionals who "
            "want a repeatable, disciplined way of working with AI coding tools.",
        )
        self.assertIn("They use large language models", projection["podwiki"]["lead"])
        self.assertEqual(
            projection["book"]["description"],
            "How Software Fails: A Field Guide to Understanding Complex System Disasters. "
            "Why Systems Break in Ways Their Creators Never Imagined.",
        )

    def test_review_routes_do_not_mutate_identity_content_or_course_state(self) -> None:
        tracked_models = (
            get_user_model(),
            ContentSource,
            ContentRelease,
            ContentDocument,
            ContentRelation,
            ContentAsset,
            Course,
            CourseRegistration,
            Enrollment,
            DurableJob,
        )
        before = {model: model.objects.count() for model in tracked_models}

        for path, _marker, _canonical in ROUTES:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)

        self.assertEqual(before, {model: model.objects.count() for model in tracked_models})
        self.assertEqual(mail.outbox, [])

    def test_projected_cohort_maps_to_existing_copied_cmp_course(self) -> None:
        legacy_course = Course.objects.create(
            slug="ai-dev-tools-2026",
            title="AI Dev Tools Zoomcamp 2026",
            description="Existing copied CMP course record",
        )
        expected_path = reverse(
            "courses:course",
            kwargs={"course_slug": legacy_course.slug},
        )

        for path in (
            "/courses/ai-dev-tools-zoomcamp/",
            "/courses/ai-dev-tools-zoomcamp/cohorts/ai-dev-tools-2026/",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertContains(response, f'href="{expected_path}"')

    def test_exact_fragment_and_query_are_preserved_and_escaped(self) -> None:
        faq_response = self.client.get("/faq/ai-dev-tools-zoomcamp.html")
        self.assertContains(faq_response, 'id="4487db3924"', count=1)

        query = '<script>alert("review")</script>'
        search_response = self.client.get("/podwiki/search/", {"q": query})
        self.assertContains(search_response, "&lt;script&gt;", html=False)
        self.assertNotContains(search_response, query, html=False)

    def test_registration_preview_post_is_explicitly_non_mutating(self) -> None:
        path = reverse("course-registration-preview-ai-dev-tools-2026")
        before = {
            "courses": Course.objects.count(),
            "registrations": CourseRegistration.objects.count(),
            "enrollments": Enrollment.objects.count(),
            "jobs": DurableJob.objects.count(),
            "email": len(mail.outbox),
        }

        response = self.client.post(path, {"email": "somebody@example.invalid"})

        self.assertEqual(response.status_code, 405)
        self.assertEqual(
            before,
            {
                "courses": Course.objects.count(),
                "registrations": CourseRegistration.objects.count(),
                "enrollments": Enrollment.objects.count(),
                "jobs": DurableJob.objects.count(),
                "email": len(mail.outbox),
            },
        )

    def test_unknown_path_remains_a_real_404(self) -> None:
        self.assertEqual(self.client.get("/no-such-review-path/").status_code, 404)


class ReviewTemplateReadabilityTests(SimpleTestCase):
    structural_tags = (
        r"(?:article|aside|div|footer|form|h[1-6]|header|li|main|nav|ol|p|section|table|"
        r"tbody|td|th|thead|tr|ul)"
    )
    compressed_patterns = (
        re.compile(rf"</{structural_tags}>\s*<{structural_tags}\b"),
        re.compile(r"{%\s*(?:for|if|elif|else|empty|endif|endfor)\b[^%]*%}\s*<"),
        re.compile(rf"</{structural_tags}>\s*{{%\s*(?:endfor|endif|else|elif|empty)\b"),
    )

    def test_target_owned_templates_keep_structural_elements_on_separate_lines(self) -> None:
        template_root = Path(settings.BASE_DIR) / "templates"
        template_paths = [
            template_root / "core/base.html",
            template_root / "core/home.html",
            template_root / "courses/course_list.html",
            *sorted((template_root / "review").glob("*.html")),
        ]

        failures: list[str] = []
        for template_path in template_paths:
            relative_path = template_path.relative_to(settings.BASE_DIR)
            for line_number, line in enumerate(template_path.read_text().splitlines(), start=1):
                if any(pattern.search(line) for pattern in self.compressed_patterns):
                    failures.append(f"{relative_path}:{line_number}")

        self.assertEqual(
            failures,
            [],
            "Keep structural HTML and Django control tags on separate source lines",
        )
