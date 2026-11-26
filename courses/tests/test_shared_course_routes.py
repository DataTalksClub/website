"""Delivery context resolution and shared course route tests (W4)."""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from courses.models import (
    Cohort,
    CohortSharedModule,
    Course,
    CurriculumSource,
    DeliveryMode,
    Enrollment,
    Homework,
    SharedCurriculum,
    SharedLesson,
    SharedModule,
)
from courses.registration import render_markdown
from courses.services.course_context import (
    resolve_delivery_context,
    context_query,
)

SHA = "a" * 40
CHECKSUM = "b" * 64


def provenance(content_id: str, path: str) -> dict[str, str]:
    return {
        "source_content_id": content_id,
        "source_path": path,
        "source_commit_sha": SHA,
        "source_checksum": CHECKSUM,
    }


class SharedWorldTestCase(TestCase):
    """One course with a shared graph and two cohorts (2026 + self-paced)."""

    def setUp(self) -> None:
        super().setUp()
        self.course = Course.objects.create(slug="llm-zoomcamp", title="LLM Zoomcamp")
        self.shared = SharedCurriculum.objects.create(
            course=self.course,
            parser_version="course-repository-v2",
            **provenance("11111111-1111-4111-8111-111111111111", "course.yaml"),
        )
        self.module = SharedModule.objects.create(
            curriculum=self.shared,
            position=0,
            slug="01-agentic-rag",
            title="Agentic RAG",
            **provenance("22222222-2222-4222-8222-222222222222", "01-agentic-rag/module.yaml"),
        )
        self.lesson = SharedLesson.objects.create(
            module=self.module,
            position=0,
            slug="01-lesson",
            title="Introduction",
            content_markdown="# Introduction\n",
            rendered_html="<h2>Introduction</h2>\n",
            **provenance("33333333-3333-4333-8333-333333333333", "01-agentic-rag/01-lesson.md"),
        )
        self.cohort_2026 = Cohort.objects.create(
            course=self.course,
            slug="llm-zoomcamp-2026",
            title="LLM Zoomcamp 2026",
            description="live",
            identifier="2026",
            year=2026,
            curriculum_format="shared",
            delivery_mode=DeliveryMode.LIVE,
        )
        self.cohort_2026.shared_curriculum = self.shared
        self.cohort_2026.save()
        self.homework = Homework.objects.create(
            course=self.cohort_2026,
            slug="hw1",
            title="Homework 1",
            due_date=timezone.now() + timedelta(days=7),
        )
        CohortSharedModule.objects.create(
            cohort=self.cohort_2026,
            shared_module=self.module,
            position=0,
            terminal_homework=self.homework,
        )
        self.cohort_self_paced = Cohort.objects.create(
            course=self.course,
            slug="llm-zoomcamp-self-paced",
            title="LLM Zoomcamp Self-Paced",
            description="self paced",
            identifier="self-paced",
            year=2027,
            curriculum_format="shared",
            delivery_mode=DeliveryMode.SELF_PACED,
        )
        self.cohort_self_paced.shared_curriculum = self.shared
        self.cohort_self_paced.save()

    def module_url(self) -> str:
        return reverse(
            "shared_module",
            kwargs={"course_slug": self.course.slug, "module_slug": self.module.slug},
        )

    def lesson_url(self) -> str:
        return reverse(
            "shared_lesson",
            kwargs={
                "course_slug": self.course.slug,
                "module_slug": self.module.slug,
                "lesson_slug": self.lesson.slug,
            },
        )


class DeliveryContextTests(SharedWorldTestCase):
    def test_explicit_query_resolves_and_beats_everything(self) -> None:
        request = self.client.get(self.module_url() + "?cohort=self-paced").wsgi_request
        context = resolve_delivery_context(request, self.course)

        self.assertEqual(context.source, "explicit")
        self.assertEqual(context.cohort.pk, self.cohort_self_paced.pk)
        self.assertTrue(context.explicit)
        self.assertTrue(context.varies_response)

    def test_duplicate_query_value_is_invalid_without_fallback(self) -> None:
        url = self.module_url() + "?cohort=2026&cohort=self-paced"
        request = self.client.get(url).wsgi_request
        context = resolve_delivery_context(request, self.course)

        self.assertEqual(context.source, "invalid")
        self.assertIsNone(context.cohort)

    def test_malformed_query_value_is_invalid(self) -> None:
        request = self.client.get(self.module_url() + "?cohort=2026!").wsgi_request
        context = resolve_delivery_context(request, self.course)

        self.assertEqual(context.source, "invalid")
        self.assertIsNotNone(context.invalid_identifier)

    def test_unknown_explicit_identifier_never_falls_back(self) -> None:
        request = self.client.get(self.module_url() + "?cohort=2099").wsgi_request
        context = resolve_delivery_context(request, self.course)

        self.assertEqual(context.source, "invalid")
        self.assertIsNone(context.cohort)

    def test_sole_enrollment_is_used_without_query(self) -> None:
        user = Enrollment.objects.create(
            student=get_user_model().objects.create_user(username="solo"),
            course=self.cohort_2026,
        ).student
        request = self.client.get(self.module_url()).wsgi_request
        request.user = user
        context = resolve_delivery_context(request, self.course)

        self.assertEqual(context.source, "sole_enrollment")
        self.assertEqual(context.cohort.pk, self.cohort_2026.pk)
        self.assertIsNotNone(context.enrollment)

    def test_multiple_enrollments_choose_not_newest(self) -> None:
        user = get_user_model().objects.create_user(username="multi")
        Enrollment.objects.create(student=user, course=self.cohort_2026)
        Enrollment.objects.create(student=user, course=self.cohort_self_paced)
        request = self.client.get(self.module_url()).wsgi_request
        request.user = user
        context = resolve_delivery_context(request, self.course)

        self.assertEqual(context.source, "chooser")
        self.assertIsNone(context.cohort)
        self.assertTrue(context.has_multiple_enrollments)

    def test_anonymous_chooser_context_stays_cache_eligible(self) -> None:
        request = self.client.get(self.module_url()).wsgi_request
        context = resolve_delivery_context(request, self.course)

        self.assertEqual(context.source, "chooser")
        self.assertFalse(context.varies_response)

    def test_context_query_helper(self) -> None:
        self.assertEqual(context_query(self.cohort_2026), "?cohort=2026")
        self.assertEqual(context_query(None), "")


class SharedRouteTests(SharedWorldTestCase):
    def test_shared_module_page_renders_anonymously(self) -> None:
        response = self.client.get(self.module_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Agentic RAG")
        self.assertContains(response, self.lesson.title)
        self.assertNotIn("cohort=", response.get("Cache-Control", ""))

        # A first-time visitor (no explicit, remembered, or sole-enrollment
        # cohort) is not asked to choose a delivery first: the module names
        # each cohort's homework for it directly.
        self.assertNotContains(response, "How are you taking this course?")
        self.assertNotContains(response, "Choose a delivery")
        self.assertContains(response, self.homework.title)
        self.assertContains(response, "2026 cohort")

    def test_shared_lesson_page_renders_and_links_siblings(self) -> None:
        SharedLesson.objects.create(
            module=self.module,
            position=1,
            slug="02-practice",
            title="Practice",
            rendered_html="<h2>Practice</h2>",
            **provenance("34444444-4444-4444-8444-444444444444", "01-agentic-rag/02-practice.md"),
        )
        response = self.client.get(self.lesson_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Practice")

    def test_two_segment_cohort_path_renders_directly_no_redirect(self) -> None:
        """The bare two-segment path is already the canonical cohort page.

        It used to one-hop redirect to a ``cohorts/<identifier>`` namespace
        (canonical 2026-09-07 to 2026-09-15); that namespace is retired now,
        so the bare path renders the cohort page directly instead.
        """

        canonical = reverse(
            "cohort",
            kwargs={
                "course_slug": self.course.slug,
                "cohort_identifier": self.cohort_2026.identifier,
            },
        )
        self.assertEqual(canonical, f"/courses/{self.course.slug}/{self.cohort_2026.identifier}")
        response = self.client.get(canonical)

        self.assertEqual(response.status_code, 200)

    def test_canonical_cohort_page_renders(self) -> None:
        response = self.client.get(
            reverse(
                "cohort",
                kwargs={
                    "course_slug": self.course.slug,
                    "cohort_identifier": self.cohort_2026.identifier,
                },
            )
        )

        self.assertEqual(response.status_code, 200)

    def test_canonical_cohort_page_renders_its_shared_modules(self) -> None:
        """A shared-format cohort's placements render on its own landing page.

        Regression test: the cohort page used to only understand the
        modules-format curriculum flow, so a shared-format cohort with real
        ``CohortSharedModule`` placements silently fell back to the legacy
        homework-table rendering and showed no module at all.
        """

        response = self.client.get(
            reverse(
                "cohort",
                kwargs={
                    "course_slug": self.course.slug,
                    "cohort_identifier": self.cohort_2026.identifier,
                },
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["is_module_curriculum"])
        flow = response.context["curriculum_flow"]
        self.assertEqual(len(flow), 1)
        self.assertEqual(flow[0].kind, "module")
        self.assertEqual(flow[0].module.pk, self.module.pk)
        self.assertIs(flow[0].homework, response.context["homeworks"][0])
        self.assertContains(response, self.module.title)
        self.assertContains(
            response,
            reverse(
                "shared_module",
                kwargs={"course_slug": self.course.slug, "module_slug": self.module.slug},
            ),
        )

    def test_cohort_page_module_link_carries_the_cohort_context(self) -> None:
        """The cohort page's module link resolves the module page's own panel.

        Regression test: the cohort page used to link into the shared module
        page with a bare path, so a reader arriving from
        ``cohorts/<identifier>`` still hit the module page's full "how are
        you taking this course" chooser -- even though the delivery was
        already unambiguous from how they got there.  The link must carry
        ``?cohort=`` the same way a shared lesson link does, so the module
        page's own delivery panel resolves and collapses to the quiet link.
        """

        response = self.client.get(
            reverse(
                "cohort",
                kwargs={
                    "course_slug": self.course.slug,
                    "cohort_identifier": self.cohort_2026.identifier,
                },
            )
        )
        flow = response.context["curriculum_flow"]
        self.assertTrue(flow[0].url.endswith(f"?cohort={self.cohort_2026.identifier}"))

        module_response = self.client.get(flow[0].url)
        self.assertEqual(module_response.status_code, 200)
        self.assertEqual(module_response.context["delivery_cohort"], self.cohort_2026)
        self.assertNotContains(module_response, "Open the cohort page")
        self.assertNotContains(module_response, "How are you taking this course?")

        # The resolved cohort also unlocks this module's own terminal homework
        # in the course rail -- a shared module's real homework
        # assignment is one cohort's placement, not something that exists
        # independent of a resolved delivery.
        self.assertEqual(module_response.context["rail_homework"], self.homework)
        self.assertContains(module_response, self.homework.title)
        self.assertContains(
            module_response,
            reverse(
                "cohort_homework",
                kwargs={
                    "course_slug": self.course.slug,
                    "cohort_identifier": self.cohort_2026.identifier,
                    "homework_slug": self.homework.slug,
                },
            ),
        )

    def test_unknown_cohort_identifier_is_a_real_404(self) -> None:
        response = self.client.get(
            reverse(
                "cohort",
                kwargs={"course_slug": self.course.slug, "cohort_identifier": "2099"},
            )
        )

        self.assertEqual(response.status_code, 404)

    def test_unknown_module_and_unknown_identifier_are_404(self) -> None:
        self.assertEqual(
            self.client.get(f"/courses/{self.course.slug}/99-not-a-module").status_code,
            404,
        )
        self.assertEqual(
            self.client.get(f"/courses/{self.course.slug}/2026/01-lesson").status_code,
            404,
        )

    def test_explicit_context_marks_response_private(self) -> None:
        response = self.client.get(self.lesson_url() + "?cohort=2026")

        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("private", response["Cache-Control"])

    def test_clean_anonymous_lesson_stays_public_cache_eligible(self) -> None:
        response = self.client.get(self.lesson_url())

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("no-store", response.get("Cache-Control", ""))

    def test_self_paced_panel_promises_no_deadlines(self) -> None:
        response = self.client.get(self.lesson_url() + "?cohort=self-paced")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "self-paced")
        self.assertNotContains(response, "Homework 1")

    def test_authenticated_read_never_creates_an_enrollment(self) -> None:
        user = get_user_model().objects.create_user(username="window-shopper")
        self.client.force_login(user)
        url = reverse(
            "cohort_homework",
            kwargs={
                "course_slug": self.course.slug,
                "cohort_identifier": self.cohort_2026.identifier,
                "homework_slug": self.homework.slug,
            },
        )

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Enrollment.objects.filter(student=user, course=self.cohort_2026).exists())

    def test_copied_url_for_a_different_cohort_grants_nothing(self) -> None:
        user = get_user_model().objects.create_user(username="copied-link")
        Enrollment.objects.create(student=user, course=self.cohort_self_paced)
        self.client.force_login(user)
        url = reverse(
            "cohort_homework",
            kwargs={
                "course_slug": self.course.slug,
                "cohort_identifier": self.cohort_2026.identifier,
                "homework_slug": self.homework.slug,
            },
        )

        response = self.client.get(url)

        # The read renders (public assignment statements are readable), but no
        # enrollment in the path's cohort may appear as a side effect.
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Enrollment.objects.filter(student=user, course=self.cohort_2026).exists())


class LessonExternalLinkTests(SharedWorldTestCase):
    """External destinations on the lesson page open in a new tab; internal ones don't."""

    def test_external_link_in_the_lesson_body_opens_in_a_new_tab(self) -> None:
        self.lesson.content_markdown = (
            "See the [external docs](https://docs.example.com/guide) and the "
            "[module overview](/courses/llm-zoomcamp/01-agentic-rag)."
        )
        self.lesson.rendered_html = render_markdown(self.lesson.content_markdown)
        self.lesson.save(update_fields=("content_markdown", "rendered_html"))

        response = self.client.get(self.lesson_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            '<a href="https://docs.example.com/guide" target="_blank" '
            'rel="noopener noreferrer">external docs</a>',
        )
        self.assertContains(
            response,
            '<a href="/courses/llm-zoomcamp/01-agentic-rag">module overview</a>',
        )

    def test_archived_delivery_notice_links_to_github_in_a_new_tab(self) -> None:
        archive_sha = "c" * 40
        archive_cohort = Cohort.objects.create(
            course=self.course,
            slug="llm-zoomcamp-2024",
            title="LLM Zoomcamp 2024",
            description="archived",
            identifier="2024",
            year=2024,
            curriculum_format="legacy",
            delivery_mode=DeliveryMode.SELF_PACED,
            curriculum_source=CurriculumSource.GITHUB_ARCHIVE,
            archive_notice_path="cohorts/2024/README.md",
            archive_url=(
                "https://github.com/DataTalksClub/llm-zoomcamp/blob/"
                + archive_sha
                + "/cohorts/2024/README.md"
            ),
            archive_commit_sha=archive_sha,
        )

        response = self.client.get(self.lesson_url() + f"?cohort={archive_cohort.identifier}")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="' + archive_cohort.archive_url + '"')
        self.assertContains(
            response,
            ('href="' + archive_cohort.archive_url + '" target="_blank" rel="noopener noreferrer"'),
        )


class ModulePageCourseRailTests(SharedWorldTestCase):
    """The module page: the course rail beside the module's syllabus (A+B)."""

    def setUp(self) -> None:
        super().setUp()
        self.lesson_2 = SharedLesson.objects.create(
            module=self.module,
            position=1,
            slug="02-search",
            title="Search",
            summary="Index the FAQ and search it with keywords.",
            rendered_html="<h2>Search</h2>",
            **provenance("35555555-5555-4555-8555-555555555555", "01-agentic-rag/02-search.md"),
        )
        self.module_2 = SharedModule.objects.create(
            curriculum=self.shared,
            position=1,
            slug="02-vector-search",
            title="Vector Search",
            **provenance("23333333-3333-4333-8333-333333333333", "02-vector-search/module.yaml"),
        )
        SharedLesson.objects.create(
            module=self.module_2,
            position=0,
            slug="01-embeddings",
            title="Embeddings",
            **provenance("36666666-6666-4666-8666-666666666666", "02-vector-search/01.md"),
        )

    def sign_in(self, *read):
        from courses.models import SharedLessonReadState

        user = get_user_model().objects.create_user(username="rail-learner")
        Enrollment.objects.create(student=user, course=self.cohort_2026)
        for lesson in read:
            SharedLessonReadState.objects.create(user=user, shared_lesson=lesson)
        self.client.force_login(user)
        return user

    def lesson_path(self, lesson) -> str:
        return reverse("shared_lesson", args=[self.course.slug, lesson.module.slug, lesson.slug])

    def test_rail_lists_every_module_with_the_current_one_open(self) -> None:
        response = self.client.get(self.module_url())
        body = response.content.decode()

        self.assertContains(response, 'data-testid="course-rail"')
        self.assertContains(response, "Course contents")
        self.assertContains(response, "2 modules · 3 lessons")
        self.assertIn('data-module="01-agentic-rag"', body)
        self.assertIn('data-module="02-vector-search"', body)
        rail = body[body.index('data-testid="course-rail"') :]
        # The current module opens into its lessons and homework; the next
        # one stays a single row.
        self.assertIn(self.lesson_path(self.lesson_2), rail)
        self.assertIn('data-testid="rail-homework"', rail)
        self.assertNotIn("Embeddings", rail)
        self.assertIn('aria-current="page"', rail[: rail.index("Introduction")])

    def test_no_eyebrows_and_no_next_module_label(self) -> None:
        response = self.client.get(self.module_url())

        self.assertNotContains(response, "Module 1 of")
        self.assertNotContains(response, "Next module")

    def test_anonymous_visitor_gets_no_progress_and_starts_lesson_one(self) -> None:
        response = self.client.get(self.module_url())

        self.assertNotContains(response, 'data-testid="module-progress"')
        self.assertNotContains(response, 'read-indicator is-read"')
        self.assertNotContains(response, 'module-number is-read"')
        self.assertEqual(response.context["primary_action"]["label"], "Start lesson 1")
        self.assertEqual(response.context["primary_action"]["url"], self.lesson_path(self.lesson))
        self.assertContains(response, 'class="cta cta-primary', count=1)

    def test_anonymous_visitor_is_sent_to_an_open_registration(self) -> None:
        from courses.models import RegistrationCampaign

        RegistrationCampaign.objects.create(
            slug="llm-2026", title="LLM Zoomcamp", current_course=self.cohort_2026
        )

        response = self.client.get(self.module_url())

        action = response.context["primary_action"]
        self.assertEqual(action["label"], "Register to start lesson 1")
        self.assertEqual(action["url"], reverse("registration_campaign", args=["llm-2026"]))

    def test_signed_in_learner_continues_with_the_first_unread_lesson(self) -> None:
        self.sign_in(self.lesson)

        response = self.client.get(self.module_url())

        self.assertEqual(response.context["primary_action"]["label"], "Continue with lesson 2")
        self.assertContains(response, 'data-testid="module-progress"')
        self.assertContains(response, "1 of 2 lessons done")
        self.assertContains(response, "1/2")
        self.assertContains(
            response, '<span class="read-indicator is-read" aria-hidden="true">✓</span>', html=False
        )

    def test_learner_who_read_every_lesson_goes_on_to_the_next_module(self) -> None:
        self.sign_in(self.lesson, self.lesson_2)

        response = self.client.get(self.module_url())

        action = response.context["primary_action"]
        self.assertEqual(action["label"], "Continue to module 2")
        self.assertTrue(
            action["url"].startswith(
                reverse("shared_module", args=[self.course.slug, self.module_2.slug])
            )
        )

    def test_syllabus_shows_lesson_descriptions_and_degrades_without_a_write_up(self) -> None:
        response = self.client.get(self.module_url())

        self.assertContains(response, "Index the FAQ and search it with keywords.")
        # Nothing imported for the write-up: those sections are not drawn.
        self.assertNotContains(response, 'data-testid="module-build"')
        self.assertNotContains(response, 'data-testid="module-extras"')
        self.assertContains(response, 'data-testid="module-homework"')

    def test_homework_is_one_row_on_the_module_page(self) -> None:
        """The steps open on the homework page itself; here it is title, status, date."""

        from courses.models import Question

        self.homework.state = "OP"
        self.homework.save(update_fields=["state"])
        Question.objects.create(homework=self.homework, text="First?", question_type="FF")
        self.sign_in()

        response = self.client.get(self.module_url())
        body = response.content.decode()

        row = body[body.index('data-testid="rail-homework"') :]
        row = row[: row.index("</li>")]
        self.assertIn(self.homework.title, row)
        self.assertIn("due", row)
        self.assertIn("status-pill", row)
        self.assertNotContains(response, 'data-testid="homework-rail-steps"')

    def test_a_long_module_shows_a_window_of_lessons_around_the_reader(self) -> None:
        from courses.models import SharedLessonReadState

        extra = [
            SharedLesson.objects.create(
                module=self.module,
                position=index,
                slug=f"{index:02d}-extra",
                title=f"Extra lesson {index}",
                **provenance(
                    f"3777777{index % 10}-7777-4777-8777-77777777777{index // 10}",
                    f"01-agentic-rag/{index:02d}-extra.md",
                ),
            )
            for index in range(2, 16)
        ]
        lessons = [self.lesson, self.lesson_2, *extra]
        user = self.sign_in()
        for lesson in lessons[:9]:
            SharedLessonReadState.objects.create(user=user, shared_lesson=lesson)

        response = self.client.get(self.module_url())
        body = response.content.decode()
        rail = body[body.index("data-rail-lessons") :]
        rail = rail[: rail.index("</ol>")]

        self.assertIn("Show all 16 lessons", rail)
        for lesson in lessons[6:12]:  # three read ones, the next one, two after
            row = rail[: rail.index(f"/{lesson.slug}")]
            self.assertNotIn("rail-unit-extra", row[row.rindex("<li") :])
        for lesson in (lessons[0], lessons[5], lessons[12], lessons[15]):
            row = rail[: rail.index(f"/{lesson.slug}")]
            self.assertIn("rail-unit-extra", row[row.rindex("<li") :])

    def test_a_short_module_shows_every_lesson(self) -> None:
        response = self.client.get(self.module_url())

        self.assertNotContains(response, ' rail-unit-extra"')
        self.assertNotContains(response, 'class="rail-more"')

    def test_anonymous_homework_row_has_no_steps(self) -> None:
        response = self.client.get(self.module_url())

        self.assertNotContains(response, 'data-testid="homework-rail-steps"')

    def test_self_paced_delivery_shows_no_other_cohorts_homework(self) -> None:
        response = self.client.get(self.module_url() + "?cohort=self-paced")

        self.assertIsNone(response.context["rail_homework"])
        self.assertNotContains(response, 'data-testid="rail-homework"')

    def test_lesson_page_carries_the_same_course_rail(self) -> None:
        response = self.client.get(self.lesson_url())
        body = response.content.decode()

        self.assertContains(response, 'data-testid="course-rail"')
        self.assertIn('data-module="02-vector-search"', body)
        rail = body[body.index('data-testid="course-rail"') :]
        self.assertIn('aria-current="page"', rail)
