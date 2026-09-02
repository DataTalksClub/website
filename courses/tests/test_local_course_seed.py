"""The local seed makes the database agree with the checked course projection."""

from __future__ import annotations

import json
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.bootstrap import RuntimeEnvironment
from core.home_content import course_catalog
from courses.course_family_catalog import cohort_family_identity
from courses.models import Cohort, Course, Homework, Project
from courses.services.local_course_seed import (
    CATALOG_SOURCE_SHA256,
    LocalCourseSeedError,
    assert_catalog_matches_projection,
    load_catalog_specs,
    load_projected_courses,
    seed_local_courses,
)


class LocalCourseSeedSourceTests(TestCase):
    def test_pinned_catalog_source_matches_the_projection_it_was_built_from(self) -> None:
        specs = load_catalog_specs()
        projected = load_projected_courses()

        assert_catalog_matches_projection(specs, projected)

        self.assertEqual(len(specs), len(projected))
        self.assertTrue(
            all(record["provenance"]["checksum"] == CATALOG_SOURCE_SHA256 for record in projected)
        )

    def test_drifted_projection_is_refused(self) -> None:
        specs = load_catalog_specs()
        projected = [dict(record) for record in load_projected_courses()]
        projected[0]["title"] = "Renamed Zoomcamp"

        with self.assertRaises(LocalCourseSeedError) as refusal:
            assert_catalog_matches_projection(specs, projected)

        self.assertEqual(str(refusal.exception), "catalog-projection-drift")


class LocalCourseSeedTests(TestCase):
    def test_seed_writes_every_projected_course_with_its_assignments(self) -> None:
        result = seed_local_courses()

        projected = {record["slug"]: record for record in load_projected_courses()}
        self.assertEqual(Cohort.objects.count(), len(projected))
        self.assertEqual(Course.objects.count(), 6)
        self.assertEqual(result.courses_created, len(projected))
        for slug, record in projected.items():
            with self.subTest(course=slug):
                course = Cohort.objects.get(slug=slug)
                family_slug, year = cohort_family_identity(slug)
                self.assertEqual(course.course.slug, family_slug)
                self.assertEqual(course.year, year)
                self.assertEqual(course.title, record["title"])
                self.assertEqual(course.finished, record["finished"])
                self.assertTrue(course.visible)
                self.assertEqual(
                    Homework.objects.filter(course=course).count(),
                    record["homework_count"],
                )
                self.assertEqual(
                    Project.objects.filter(course=course).count(),
                    record["project_count"],
                )

    def test_seed_is_idempotent(self) -> None:
        first = seed_local_courses()
        counts = (Cohort.objects.count(), Homework.objects.count(), Project.objects.count())

        second = seed_local_courses()

        self.assertEqual(
            (Cohort.objects.count(), Homework.objects.count(), Project.objects.count()),
            counts,
        )
        self.assertEqual(second.course_count, first.course_count)
        self.assertEqual(second.courses_created, 0)
        self.assertEqual(second.homeworks_created, 0)
        self.assertEqual(second.projects_created, 0)

    def test_seed_preserves_locally_owned_operational_state(self) -> None:
        seed_local_courses()
        course = Cohort.objects.get(slug="de-zoomcamp-2026")
        course.registration_url = "https://courses.datatalks.club/de-zoomcamp-2026/register"
        course.first_homework_scored = True
        course.save()
        homework = Homework.objects.filter(course=course).order_by("due_date").first()
        assert homework is not None
        homework.state = "SC"
        homework.save()

        seed_local_courses()

        course.refresh_from_db()
        homework.refresh_from_db()
        self.assertEqual(
            course.registration_url,
            "https://courses.datatalks.club/de-zoomcamp-2026/register",
        )
        self.assertTrue(course.first_homework_scored)
        self.assertEqual(homework.state, "SC")

    def test_seed_does_not_replace_imported_assignments(self) -> None:
        seed_local_courses()
        course = Cohort.objects.get(slug="de-zoomcamp-2026")
        course.description = "Imported course description."
        course.save()
        Homework.objects.filter(course=course).delete()
        Project.objects.filter(course=course).delete()
        due = timezone.now()
        imported_homework = Homework.objects.create(
            course=course,
            slug="hw1",
            title="Imported homework",
            description="Real homework description.",
            due_date=due,
        )
        imported_project = Project.objects.create(
            course=course,
            slug="project1",
            title="Imported project",
            description="Real project description.",
            submission_due_date=due,
            peer_review_due_date=due,
        )

        seed_local_courses()

        course.refresh_from_db()
        imported_homework.refresh_from_db()
        imported_project.refresh_from_db()
        self.assertEqual(course.description, "Imported course description.")
        self.assertEqual(Homework.objects.filter(course=course).count(), 1)
        self.assertEqual(Project.objects.filter(course=course).count(), 1)
        self.assertEqual(imported_homework.description, "Real homework description.")
        self.assertEqual(imported_project.description, "Real project description.")
        self.assertFalse(
            Homework.objects.filter(
                course=course, description__startswith="Practice assignment for"
            ).exists()
        )

    def test_homepage_catalog_links_resolve_to_seeded_courses(self) -> None:
        seed_local_courses()

        for entry in course_catalog():
            with self.subTest(course=entry.slug):
                family_slug, year = cohort_family_identity(entry.slug)
                self.assertEqual(entry.public_path, f"/courses/{family_slug}/{year}")
                course = Cohort.objects.get(slug=entry.slug)
                self.assertEqual(
                    Homework.objects.filter(course=course).count(),
                    entry.homework_count,
                )
                self.assertEqual(
                    Project.objects.filter(course=course).count(),
                    entry.project_count,
                )
                response = self.client.get(entry.public_path)
                self.assertEqual(response.status_code, 200)

    def test_course_index_lists_the_seeded_catalog(self) -> None:
        seed_local_courses()

        response = self.client.get(reverse("course_list"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "No active courses right now.")
        for entry in course_catalog():
            with self.subTest(course=entry.slug):
                self.assertContains(response, entry.title)


class LocalCourseSeedRefusalTests(TestCase):
    @override_settings(RUNTIME_ENVIRONMENT=RuntimeEnvironment.DEVELOPMENT)
    def test_deployed_environment_is_refused(self) -> None:
        with self.assertRaises(LocalCourseSeedError) as refusal:
            seed_local_courses()

        self.assertEqual(str(refusal.exception), "environment-not-local")
        self.assertEqual(Cohort.objects.count(), 0)

    @override_settings(RUNTIME_ENVIRONMENT=RuntimeEnvironment.PRODUCTION)
    def test_production_environment_is_refused(self) -> None:
        with self.assertRaises(LocalCourseSeedError):
            seed_local_courses()

        self.assertEqual(Cohort.objects.count(), 0)

    def test_non_sqlite_database_is_refused(self) -> None:
        databases = {
            "default": {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": "unused",
            }
        }
        with override_settings(DATABASES=databases):
            with self.assertRaises(LocalCourseSeedError) as refusal:
                seed_local_courses()

        self.assertEqual(str(refusal.exception), "database-not-local-sqlite")
        self.assertEqual(Cohort.objects.count(), 0)


class SeedLocalCoursesCommandTests(TestCase):
    def test_command_reports_what_it_wrote(self) -> None:
        stdout = StringIO()

        call_command("seed_local_courses", stdout=stdout)

        summary = json.loads(stdout.getvalue())
        self.assertTrue(summary["written"])
        self.assertEqual(summary["courses"], Cohort.objects.count())
        self.assertEqual(summary["courses_created"], Cohort.objects.count())
        self.assertEqual(summary["source_sha256"], CATALOG_SOURCE_SHA256)

    def test_check_validates_without_writing(self) -> None:
        stdout = StringIO()

        call_command("seed_local_courses", "--check", stdout=stdout)

        summary = json.loads(stdout.getvalue())
        self.assertFalse(summary["written"])
        self.assertEqual(summary["checked"], len(load_catalog_specs()))
        self.assertEqual(Cohort.objects.count(), 0)

    @override_settings(RUNTIME_ENVIRONMENT=RuntimeEnvironment.PRODUCTION)
    def test_command_fails_closed_outside_local_development(self) -> None:
        with self.assertRaises(CommandError) as refusal:
            call_command("seed_local_courses", stdout=StringIO())

        self.assertEqual(str(refusal.exception), "environment-not-local")
        self.assertEqual(Cohort.objects.count(), 0)
