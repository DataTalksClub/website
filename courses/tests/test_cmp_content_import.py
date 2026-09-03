"""Tests for the CMP content importer.

The source is a synthetic SQLite database built here.  No real export is read, and no
table carrying personal data is created, so the suite cannot depend on production data
being present on the machine.
"""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase

from courses.models import (
    Cohort,
    Course,
    Enrollment,
    Homework,
    Module,
    Project,
    Question,
    ReviewCriteria,
    Submission,
)
from courses.models.cohort import CourseRegistration, RegistrationCampaign
from courses.services.cmp_content_import import (
    SKIPPED_COHORTS,
    CmpContentImportError,
    import_cmp_course_content,
)

_SCHEMA = """
CREATE TABLE courses_course (
    id INTEGER, slug TEXT, title TEXT, description TEXT, social_media_hashtag TEXT,
    faq_document_url TEXT, first_homework_scored INTEGER, finished INTEGER,
    homework_problems_comments_field INTEGER, project_passing_score INTEGER,
    min_projects_to_pass INTEGER, visible INTEGER, end_date TEXT, registration_url TEXT,
    github_repo_url TEXT, start_date TEXT
);
CREATE TABLE courses_homework (
    id INTEGER, slug TEXT, title TEXT, description TEXT, due_date TEXT,
    learning_in_public_cap INTEGER, homework_url_field INTEGER,
    time_spent_lectures_field INTEGER, time_spent_homework_field INTEGER,
    faq_contribution_field INTEGER, course_id INTEGER, state TEXT, instructions_url TEXT
);
CREATE TABLE courses_question (
    id INTEGER, text TEXT, question_type TEXT, answer_type TEXT, possible_answers TEXT,
    correct_answer TEXT, scores_for_correct_answer INTEGER, homework_id INTEGER
);
CREATE TABLE courses_project (
    id INTEGER, slug TEXT, title TEXT, description TEXT, submission_due_date TEXT,
    learning_in_public_cap_project INTEGER, peer_review_due_date TEXT,
    time_spent_project_field INTEGER, problems_comments_field INTEGER,
    faq_contribution_field INTEGER, learning_in_public_cap_review INTEGER,
    number_of_peers_to_evaluate INTEGER, time_spent_evaluation_field INTEGER, state TEXT,
    course_id INTEGER, points_for_peer_review INTEGER, instructions_url TEXT
);
CREATE TABLE courses_reviewcriteria (
    id INTEGER, description TEXT, options TEXT, review_criteria_type TEXT, course_id INTEGER
);
CREATE TABLE courses_registrationcampaign (
    id INTEGER, slug TEXT, title TEXT, edition_label TEXT, is_active INTEGER,
    marketing_markdown TEXT, meta_description TEXT, hero_image_url TEXT, video_url TEXT,
    created_at TEXT, updated_at TEXT, current_course_id INTEGER
);
CREATE TABLE courses_enrollment (id INTEGER, course_id INTEGER);
CREATE TABLE courses_submission (id INTEGER, homework_id INTEGER);
CREATE TABLE courses_projectsubmission (id INTEGER, project_id INTEGER);
"""

_DUE = "2026-01-15 12:00:00+00"
# The content id a course repository's homework.yaml declares.
CONTENT_ID = "85555555-5555-4555-8555-555555555555"


def _build_source(
    path: Path,
    *,
    cohort_slugs: tuple[str, ...],
    campaigns: tuple[tuple[str, str], ...] = (),
) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(_SCHEMA)
    positions = {slug: index for index, slug in enumerate(cohort_slugs, start=1)}
    for index, (campaign_slug, promoted) in enumerate(campaigns, start=1):
        connection.execute(
            "INSERT INTO courses_registrationcampaign VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                index,
                campaign_slug,
                f"Campaign {campaign_slug}",
                "2026 cohort",
                1,
                f"Register for {campaign_slug}.",
                f"Meta for {campaign_slug}.",
                "",
                "",
                "2026-07-01 00:00:00+00",
                "2026-07-01 00:00:00+00",
                positions.get(promoted),
            ),
        )
    for index, slug in enumerate(cohort_slugs, start=1):
        connection.execute(
            "INSERT INTO courses_course VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                index,
                slug,
                f"Title {slug}",
                f"Real description for {slug}",
                "hashtag",
                "https://example.com/faq",
                1,
                0,
                0,
                70,
                1,
                1,
                "2026-05-11",
                "",
                "",
                "2026-01-12",
            ),
        )
        connection.execute(
            "INSERT INTO courses_homework VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                index * 10,
                "hw1",
                "Homework 1: Real",
                "Real homework copy",
                _DUE,
                7,
                1,
                1,
                1,
                1,
                index,
                "CL",
                "",
            ),
        )
        connection.execute(
            "INSERT INTO courses_question VALUES (?,?,?,?,?,?,?,?)",
            (index * 100, "A real question", "MC", "ANY", "a,b,c", "1", 1, index * 10),
        )
        connection.execute(
            "INSERT INTO courses_project VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                index * 10,
                "project1",
                "Project Attempt 1",
                "Real project copy",
                _DUE,
                14,
                _DUE,
                1,
                1,
                1,
                2,
                3,
                1,
                "CL",
                index,
                3,
                "",
            ),
        )
        connection.execute(
            "INSERT INTO courses_reviewcriteria VALUES (?,?,?,?,?)",
            (index * 10, "Is it reproducible?", '["no","yes"]', "RS", index),
        )
        connection.execute("INSERT INTO courses_enrollment VALUES (?,?)", (index, index))
    connection.commit()
    connection.close()


class CmpContentImportTests(TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.source = Path(self.directory.name) / "source.sqlite3"

    def _cohort(self, slug: str, *, curriculum_format: str = "legacy") -> Cohort:
        family_slug = slug.rsplit("-", 1)[0]
        family, _ = Course.objects.get_or_create(slug=family_slug, defaults={"title": family_slug})
        return Cohort.objects.create(
            course=family,
            slug=slug,
            identifier=slug.rsplit("-", 1)[1],
            year=int(slug.rsplit("-", 1)[1]),
            title=f"Seeded {slug}",
            description="Seeded description",
            curriculum_format=curriculum_format,
        )

    def _seed_placeholder(self, cohort: Cohort) -> tuple[Homework, Project]:
        now = datetime.now(timezone.utc)
        homework = Homework.objects.create(
            course=cohort,
            slug="homework-01-week-1",
            title="Week 1",
            description="Practice assignment for Week 1.",
            due_date=now + timedelta(days=7),
        )
        project = Project.objects.create(
            course=cohort,
            slug="project-01-project-attempt-1",
            title="Project Attempt 1",
            description="Production-like generated project: Project Attempt 1",
            submission_due_date=now + timedelta(days=30),
            peer_review_due_date=now + timedelta(days=37),
        )
        return homework, project

    def test_replaces_seeded_placeholders_with_real_content(self) -> None:
        cohort = self._cohort("de-zoomcamp-2026")
        self._seed_placeholder(cohort)
        _build_source(self.source, cohort_slugs=("de-zoomcamp-2026",))

        result = import_cmp_course_content(self.source)

        self.assertEqual(result.summary()["cohorts_imported"], 1)
        self.assertEqual(
            list(Homework.objects.filter(course=cohort).values_list("slug", flat=True)),
            ["hw1"],
        )
        self.assertEqual(
            list(Project.objects.filter(course=cohort).values_list("slug", flat=True)),
            ["project1"],
        )
        self.assertEqual(Question.objects.filter(homework__course=cohort).count(), 1)
        self.assertEqual(ReviewCriteria.objects.filter(course=cohort).count(), 1)
        self.assertFalse(
            Homework.objects.filter(description__startswith="Practice assignment for").exists()
        )
        self.assertFalse(
            Project.objects.filter(description__startswith="Production-like generated").exists()
        )

    def test_adopts_the_cohort_description_cmp_publishes(self) -> None:
        cohort = self._cohort("de-zoomcamp-2026")
        _build_source(self.source, cohort_slugs=("de-zoomcamp-2026",))

        import_cmp_course_content(self.source)

        cohort.refresh_from_db()
        self.assertEqual(cohort.description, "Real description for de-zoomcamp-2026")
        self.assertEqual(cohort.start_date, date(2026, 1, 12))
        self.assertEqual(cohort.end_date, date(2026, 5, 11))

    def test_running_twice_changes_nothing(self) -> None:
        cohort = self._cohort("de-zoomcamp-2026")
        self._seed_placeholder(cohort)
        _build_source(self.source, cohort_slugs=("de-zoomcamp-2026",))

        import_cmp_course_content(self.source)
        first = self._snapshot(cohort)
        second_result = import_cmp_course_content(self.source)

        self.assertEqual(self._snapshot(cohort), first)
        summary = second_result.summary()
        self.assertEqual(summary["homework_removed"], 0)
        self.assertEqual(summary["projects_removed"], 0)

    def _snapshot(self, cohort: Cohort) -> tuple:
        return (
            sorted(Homework.objects.filter(course=cohort).values_list("slug", "title")),
            sorted(Project.objects.filter(course=cohort).values_list("slug", "title")),
            sorted(Question.objects.filter(homework__course=cohort).values_list("text", flat=True)),
            ReviewCriteria.objects.filter(course=cohort).count(),
        )

    def test_skips_the_cohorts_the_owner_deferred_with_a_reason_each(self) -> None:
        deferred = tuple(SKIPPED_COHORTS)
        _build_source(self.source, cohort_slugs=deferred)

        result = import_cmp_course_content(self.source)

        self.assertEqual(result.summary()["cohorts_imported"], 0)
        skipped = dict(result.skipped_by_owner)
        self.assertEqual(set(skipped), set(deferred))
        for reason in skipped.values():
            self.assertTrue(reason.strip())
        self.assertFalse(Cohort.objects.exists())

    def test_counts_the_dependent_rows_a_skipped_cohort_would_have_dragged_in(self) -> None:
        _build_source(self.source, cohort_slugs=("ai-hero-2025",))

        result = import_cmp_course_content(self.source)

        self.assertEqual(result.skipped_dependent_rows["ai-hero-2025"], 1)

    def _modules_cohort(self, repository_homework: dict[str, str]) -> tuple[Cohort, dict]:
        """Build a modules cohort whose modules terminate in repository homework."""

        cohort = self._cohort("llm-zoomcamp-2026", curriculum_format="modules")
        modules = {}
        for position, (slug, title) in enumerate(repository_homework.items()):
            homework = Homework.objects.create(
                course=cohort,
                slug=slug,
                title=title,
                description="From the course repository",
                due_date=datetime.now(timezone.utc) + timedelta(days=7),
            )
            modules[slug] = Module.objects.create(
                cohort=cohort,
                position=position,
                slug=f"0{position + 1}-module",
                title=f"Module {position + 1}",
                terminal_homework=homework,
            )
        return cohort, modules

    def test_adopts_the_cmp_slug_and_repoints_the_module_binding(self) -> None:
        """CMP owns homework identity, so its slug replaces the repository's."""

        cohort, modules = self._modules_cohort({"homework-01": "Homework 1: Real"})
        _build_source(self.source, cohort_slugs=("llm-zoomcamp-2026",))

        result = import_cmp_course_content(self.source)

        self.assertEqual(
            list(Homework.objects.filter(course=cohort).values_list("slug", flat=True)),
            ["hw1"],
        )
        module = modules["homework-01"]
        module.refresh_from_db()
        self.assertEqual(module.terminal_homework.slug, "hw1")
        self.assertEqual(module.terminal_homework.question_set.count(), 1)
        self.assertEqual(
            result.summary()["rebindings"],
            [
                {
                    "cohort": "llm-zoomcamp-2026",
                    "module": "01-module",
                    "was": "homework-01",
                    "now": "hw1",
                }
            ],
        )

    def test_keeps_the_binding_untouched_when_the_slugs_already_agree(self) -> None:
        cohort, modules = self._modules_cohort({"hw1": "Homework 1: Real"})
        _build_source(self.source, cohort_slugs=("llm-zoomcamp-2026",))

        result = import_cmp_course_content(self.source)

        module = modules["hw1"]
        module.refresh_from_db()
        self.assertEqual(module.terminal_homework.slug, "hw1")
        self.assertEqual(result.summary()["rebindings"], [])
        self.assertEqual(Homework.objects.filter(course=cohort).count(), 1)

    def test_leaves_an_unpairable_homework_unbound_rather_than_guessing(self) -> None:
        """A wrong attachment renders as a page that looks fine and is wrong.

        Pairing by ordinal position would bind CMP's ``dlt`` workshop to whichever
        module happened to sit at its index, so a homework whose slug and title both
        disagree is reported instead.
        """

        cohort, modules = self._modules_cohort({"homework-01": "Homework 1: Drifted title"})
        _build_source(self.source, cohort_slugs=("llm-zoomcamp-2026",))

        result = import_cmp_course_content(self.source)

        module = modules["homework-01"]
        module.refresh_from_db()
        self.assertEqual(module.terminal_homework.slug, "homework-01")
        summary = result.summary()
        self.assertEqual(summary["unpaired_cmp_homework"], ["hw1"])
        self.assertEqual(summary["unpaired_repository_homework"], ["homework-01"])
        self.assertEqual(
            sorted(Homework.objects.filter(course=cohort).values_list("slug", flat=True)),
            ["homework-01", "hw1"],
        )

    def test_never_deletes_repository_homework_cmp_does_not_have(self) -> None:
        """CMP owning identity is not CMP asserting the assignment does not exist.

        Deleting it would strip its module's page, so a modules cohort keeps it and the
        divergence is reported.
        """

        cohort, _ = self._modules_cohort(
            {"hw1": "Homework 1: Real", "homework-99": "Homework 99: Repository only"}
        )
        _build_source(self.source, cohort_slugs=("llm-zoomcamp-2026",))

        result = import_cmp_course_content(self.source)

        self.assertTrue(Homework.objects.filter(course=cohort, slug="homework-99").exists())
        self.assertEqual(result.summary()["unpaired_repository_homework"], ["homework-99"])

    def _own(self, homework: Homework, *, content_id: str) -> Homework:
        """Stamp the provenance a course-repository pull writes."""

        homework.source_content_id = content_id
        homework.source_path = "cohorts/2026/01-agentic-rag/homework.yaml"
        homework.source_commit_sha = "a" * 40
        homework.source_checksum = "b" * 64
        homework.instructions_markdown = "# Homework 1\n\nRepository instructions."
        homework.instructions_source_path = "cohorts/2026/01-agentic-rag/homework.md"
        homework.save()
        return homework

    def test_the_reconciled_row_stays_the_one_the_repository_import_owns(self) -> None:
        """Adopting CMP's slug must not orphan the row from its repository.

        Replacing the row dropped ``source_content_id``, so the next repository pull saw
        a homework no import owned holding the slug it declares and refused with
        ``homework_slug_collision`` -- and the imported instructions Markdown and its
        source path went with it.
        """

        cohort, modules = self._modules_cohort({"homework-01": "Homework 1: Real"})
        owned = self._own(modules["homework-01"].terminal_homework, content_id=CONTENT_ID)
        _build_source(self.source, cohort_slugs=("llm-zoomcamp-2026",))

        import_cmp_course_content(self.source)

        reconciled = Homework.objects.get(course=cohort, slug="hw1")
        self.assertEqual(reconciled.pk, owned.pk)
        self.assertEqual(str(reconciled.source_content_id), CONTENT_ID)
        self.assertEqual(
            reconciled.instructions_source_path,
            "cohorts/2026/01-agentic-rag/homework.md",
        )
        self.assertEqual(reconciled.title, "Homework 1: Real")
        self.assertEqual(reconciled.description, "Real homework copy")

    def test_a_repository_pull_after_an_import_does_not_leave_two_rows(self) -> None:
        """The pairing has to survive a replay, or the reconciliation undoes itself.

        The pull re-creates its own row, and the local dataset copies CMP *before* it
        pulls at all, so the CMP-slugged row is usually already there when the pairing
        runs. Trying the title match only when the row is new left both identities in
        place for the same assignment, permanently.
        """

        cohort, modules = self._modules_cohort({"homework-01": "Homework 1: Real"})
        self._own(modules["homework-01"].terminal_homework, content_id=CONTENT_ID)
        Homework.objects.create(
            course=cohort,
            slug="hw1",
            title="Homework 1: Real",
            due_date=datetime.now(timezone.utc) + timedelta(days=7),
        )
        _build_source(self.source, cohort_slugs=("llm-zoomcamp-2026",))

        result = import_cmp_course_content(self.source)

        self.assertEqual(
            list(Homework.objects.filter(course=cohort).values_list("slug", flat=True)),
            ["hw1"],
        )
        survivor = Homework.objects.get(course=cohort, slug="hw1")
        self.assertEqual(str(survivor.source_content_id), CONTENT_ID)
        module = modules["homework-01"]
        module.refresh_from_db()
        self.assertEqual(module.terminal_homework_id, survivor.pk)
        self.assertEqual(survivor.question_set.count(), 1)
        self.assertEqual(result.summary()["unpaired_repository_homework"], [])

    def test_a_duplicate_carrying_submissions_is_reported_rather_than_folded(self) -> None:
        """Folding discards a row, and a discarded row takes its submissions with it."""

        cohort, modules = self._modules_cohort({"homework-01": "Homework 1: Real"})
        self._own(modules["homework-01"].terminal_homework, content_id=CONTENT_ID)
        submitted = Homework.objects.create(
            course=cohort,
            slug="hw1",
            title="Homework 1: Real",
            due_date=datetime.now(timezone.utc) + timedelta(days=7),
        )
        student = get_user_model().objects.create_user(
            username="learner", email="learner@example.com", password="x"
        )
        enrollment = Enrollment.objects.create(student=student, course=cohort)
        Submission.objects.create(homework=submitted, student=student, enrollment=enrollment)
        _build_source(self.source, cohort_slugs=("llm-zoomcamp-2026",))

        result = import_cmp_course_content(self.source)

        self.assertEqual(
            sorted(Homework.objects.filter(course=cohort).values_list("slug", flat=True)),
            ["homework-01", "hw1"],
        )
        self.assertTrue(Submission.objects.filter(homework=submitted).exists())
        self.assertEqual(result.summary()["unpaired_repository_homework"], ["homework-01"])

    def test_skips_a_cohort_the_local_catalogue_does_not_have(self) -> None:
        _build_source(self.source, cohort_slugs=("de-zoomcamp-2026",))

        result = import_cmp_course_content(self.source)

        self.assertEqual(result.skipped_not_in_local_catalogue, ("de-zoomcamp-2026",))

    def test_excludes_upstream_fixture_courses(self) -> None:
        _build_source(self.source, cohort_slugs=("fake-course", "fake-course-2"))

        result = import_cmp_course_content(self.source)

        self.assertEqual(result.skipped_fixture, ("fake-course", "fake-course-2"))

    def test_refuses_an_unreadable_source_without_naming_it(self) -> None:
        missing = Path(self.directory.name) / "absent.sqlite3"

        with self.assertRaises(CmpContentImportError) as raised:
            import_cmp_course_content(missing)

        self.assertEqual(str(raised.exception), "source-unreadable")
        self.assertNotIn(str(missing), str(raised.exception))


class CmpReviewedCohortAdoptionTests(CmpContentImportTests):
    """A cohort CMP publishes and the local catalogue lacks, under a reviewed identity."""

    def test_creates_a_missing_cohort_under_its_reviewed_family_and_year(self) -> None:
        family = Course.objects.create(slug="sma-zoomcamp", title="Stock Markets Zoomcamp")
        _build_source(self.source, cohort_slugs=("sma-zoomcamp-2026",))

        result = import_cmp_course_content(self.source)

        cohort = Cohort.objects.get(slug="sma-zoomcamp-2026")
        self.assertEqual(cohort.course, family)
        self.assertEqual((cohort.year, cohort.identifier), (2026, "2026"))
        self.assertEqual(cohort.title, "Title sma-zoomcamp-2026")
        self.assertEqual(result.created_cohorts, ("sma-zoomcamp-2026",))
        self.assertEqual(result.summary()["cohorts_imported"], 1)
        self.assertTrue(Homework.objects.filter(course=cohort).exists())

    def test_adoption_replays_without_creating_a_second_cohort(self) -> None:
        Course.objects.create(slug="sma-zoomcamp", title="Stock Markets Zoomcamp")
        _build_source(self.source, cohort_slugs=("sma-zoomcamp-2026",))

        import_cmp_course_content(self.source)
        second = import_cmp_course_content(self.source)

        self.assertEqual(Cohort.objects.filter(slug="sma-zoomcamp-2026").count(), 1)
        self.assertEqual(second.created_cohorts, ())

    def test_never_mints_a_family_to_adopt_a_cohort(self) -> None:
        _build_source(self.source, cohort_slugs=("sma-zoomcamp-2026",))

        result = import_cmp_course_content(self.source)

        self.assertEqual(result.skipped_not_in_local_catalogue, ("sma-zoomcamp-2026",))
        self.assertFalse(Course.objects.exists())
        self.assertFalse(Cohort.objects.exists())

    def test_never_adopts_a_slug_the_reviewers_have_not_ruled_on(self) -> None:
        Course.objects.create(slug="unreviewed", title="Unreviewed")
        _build_source(self.source, cohort_slugs=("unreviewed-2026",))

        result = import_cmp_course_content(self.source)

        self.assertEqual(result.skipped_not_in_local_catalogue, ("unreviewed-2026",))
        self.assertFalse(Cohort.objects.exists())


class CmpRegistrationCampaignImportTests(CmpContentImportTests):
    """Campaign definitions arrive; the learner rows that reference them never do."""

    def test_imports_the_campaign_definition_and_links_the_cohort_it_promotes(self) -> None:
        cohort = self._cohort("de-zoomcamp-2026")
        _build_source(
            self.source,
            cohort_slugs=("de-zoomcamp-2026",),
            campaigns=(("de-zoomcamp", "de-zoomcamp-2026"),),
        )

        result = import_cmp_course_content(self.source)

        campaign = RegistrationCampaign.objects.get(slug="de-zoomcamp")
        self.assertEqual(campaign.current_course, cohort)
        self.assertEqual(campaign.title, "Campaign de-zoomcamp")
        self.assertEqual(campaign.edition_label, "2026 cohort")
        self.assertTrue(campaign.is_active)
        self.assertEqual(campaign.marketing_markdown, "Register for de-zoomcamp.")
        summary = result.summary()
        self.assertEqual(summary["campaigns_written"], 1)
        self.assertEqual(summary["campaigns_created"], 1)
        self.assertEqual(
            summary["campaigns"],
            [
                {"slug": "de-zoomcamp", "created": True, "promotes": "de-zoomcamp-2026"},
            ],
        )

    def test_a_campaign_promoting_no_cohort_arrives_unlinked_rather_than_refused(self) -> None:
        self._cohort("de-zoomcamp-2026")
        _build_source(
            self.source,
            cohort_slugs=("de-zoomcamp-2026",),
            campaigns=(("mlops-zoomcamp", ""),),
        )

        result = import_cmp_course_content(self.source)

        campaign = RegistrationCampaign.objects.get(slug="mlops-zoomcamp")
        self.assertIsNone(campaign.current_course)
        self.assertEqual(result.campaigns_without_a_local_cohort, ())

    def test_reports_a_campaign_whose_cohort_this_database_does_not_hold(self) -> None:
        _build_source(
            self.source,
            cohort_slugs=("ai-hero-2026",),
            campaigns=(("ai-hero", "ai-hero-2026"),),
        )

        result = import_cmp_course_content(self.source)

        self.assertEqual(result.campaigns_without_a_local_cohort, ("ai-hero",))
        self.assertIsNone(RegistrationCampaign.objects.get(slug="ai-hero").current_course)

    def test_running_twice_writes_no_second_campaign(self) -> None:
        self._cohort("de-zoomcamp-2026")
        _build_source(
            self.source,
            cohort_slugs=("de-zoomcamp-2026",),
            campaigns=(("de-zoomcamp", "de-zoomcamp-2026"),),
        )

        import_cmp_course_content(self.source)
        before = RegistrationCampaign.objects.get(slug="de-zoomcamp").updated_at
        second = import_cmp_course_content(self.source)

        self.assertEqual(RegistrationCampaign.objects.count(), 1)
        self.assertEqual(second.summary()["campaigns_created"], 0)
        self.assertEqual(RegistrationCampaign.objects.get(slug="de-zoomcamp").updated_at, before)

    def test_never_reads_or_writes_a_learner_registration(self) -> None:
        self._cohort("de-zoomcamp-2026")
        _build_source(
            self.source,
            cohort_slugs=("de-zoomcamp-2026",),
            campaigns=(("de-zoomcamp", "de-zoomcamp-2026"),),
        )

        import_cmp_course_content(self.source)

        self.assertEqual(CourseRegistration.objects.count(), 0)
