from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import UUID

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from content_sync.course_repository import parse_course_repository
from courses.models import (
    Cohort,
    Course,
    CurriculumFlowItem,
    CurriculumFormat,
    Homework,
    HomeworkState,
    Module,
    Project,
    ProjectState,
    RegistrationCampaign,
    Unit,
)
from courses.services.curriculum_import import (
    CurriculumImportCommand,
    import_course_repository_curriculum,
)
from courses.services.local_course_modules import (
    TARGET_COHORTS,
    LocalCourseModulesError,
    select_target_cohort,
    snapshot_checksum,
    target_source_graph,
)

FIXTURE_ROOT = (
    Path(__file__).parents[2]
    / "content_sync"
    / "tests"
    / "fixtures"
    / "course_repository"
    / "llm_zoomcamp_2026"
)
COMMIT_SHA = "c" * 40


def fixture_source():
    snapshot = {
        path.relative_to(FIXTURE_ROOT).as_posix(): path.read_bytes()
        for path in FIXTURE_ROOT.rglob("*")
        if path.is_file()
    }
    return parse_course_repository(snapshot, commit_sha=COMMIT_SHA)


class LocalCourseModuleSelectionTests(TestCase):
    def test_only_the_three_reviewed_2026_cohorts_are_selectable(self):
        source = fixture_source()
        source_cohort = next(cohort for cohort in source.cohorts if cohort.identifier == "2026")

        for source_stable_id, cohort_identifier in TARGET_COHORTS.items():
            with self.subTest(source_stable_id=source_stable_id):
                candidate = replace(
                    source,
                    course=replace(source.course, slug=source_stable_id),
                    cohorts=(replace(source_cohort, course_slug=source_stable_id),),
                )
                selected = select_target_cohort(
                    candidate,
                    source_stable_id=source_stable_id,
                    cohort_identifier=cohort_identifier,
                )
                self.assertEqual(selected.identifier, "2026")
                self.assertEqual(selected.format, CurriculumFormat.MODULES)

        with self.assertRaises(LocalCourseModulesError) as raised:
            select_target_cohort(
                source,
                source_stable_id="llm-zoomcamp",
                cohort_identifier="2025",
            )
        self.assertEqual(str(raised.exception), "cohort_selection_invalid")

    def test_target_graph_filters_legacy_cohorts_and_applies_explicit_homework_mapping(self):
        source = fixture_source()
        homework_path = "cohorts/2026/01-agentic-rag/homework.yaml"
        graph = target_source_graph(
            source,
            source_stable_id="llm-zoomcamp",
            cohort_identifier="2026",
            homework_slug_overrides={homework_path: "existing-homework"},
        )

        self.assertEqual(
            [(cohort.identifier, cohort.format) for cohort in graph.cohorts],
            [("2026", "modules")],
        )
        self.assertEqual(len(graph.modules), 1)
        self.assertEqual(len(graph.homeworks), 1)
        self.assertEqual(graph.homeworks[0].slug, "existing-homework")
        self.assertEqual(graph.cohorts[0].flow[0].homework.slug, "existing-homework")

    def test_snapshot_checksum_is_order_independent(self):
        checksums = {"course.yaml": "a" * 64, "README.md": "b" * 64}
        self.assertEqual(
            snapshot_checksum(checksums),
            snapshot_checksum({"README.md": "b" * 64, "course.yaml": "a" * 64}),
        )


class LocalCourseModuleAdoptionTests(TestCase):
    def make_existing_target(self):
        course = Course.objects.create(slug="llm-zoomcamp", title="Existing LLM course")
        cohort = Cohort.objects.create(
            course=course,
            slug="llm-zoomcamp-2026",
            identifier="2026",
            year=2026,
            title="Operator LLM cohort",
            description="Operator-owned description",
            curriculum_format=CurriculumFormat.LEGACY,
            start_date=timezone.localdate() - timedelta(days=4),
            end_date=timezone.localdate() + timedelta(days=40),
            registration_url="https://example.com/llm-register",
            finished=True,
            first_homework_scored=True,
            min_projects_to_pass=2,
            project_passing_score=7,
        )
        due_date = timezone.now() + timedelta(days=11)
        homework = Homework.objects.create(
            course=cohort,
            slug="existing-homework",
            title="Operator homework title",
            description="Operator homework description",
            instructions_markdown="Operator instructions",
            instructions_url="https://example.com/instructions",
            due_date=due_date,
            state=HomeworkState.SCORED.value,
        )
        project = Project.objects.create(
            course=cohort,
            slug="project-01",
            title="Operator project title",
            state=ProjectState.PEER_REVIEWING.value,
            submission_due_date=timezone.now() + timedelta(days=20),
            peer_review_due_date=timezone.now() + timedelta(days=27),
        )
        campaign = RegistrationCampaign.objects.create(
            slug="llm-zoomcamp-registration",
            title="LLM registration",
            current_course=cohort,
        )
        return course, cohort, homework, project, campaign

    def test_adoption_preserves_existing_records_and_legacy_cohorts(self):
        course, cohort, homework, project, campaign = self.make_existing_target()
        source = target_source_graph(
            fixture_source(),
            source_stable_id="llm-zoomcamp",
            cohort_identifier="2026",
            homework_slug_overrides={
                "cohorts/2026/01-agentic-rag/homework.yaml": "existing-homework"
            },
        )
        old_start = cohort.start_date
        old_end = cohort.end_date
        old_homework = (
            homework.slug,
            homework.title,
            homework.description,
            homework.instructions_markdown,
            homework.instructions_url,
            homework.due_date,
            homework.state,
        )
        old_project = (
            project.title,
            project.state,
            project.submission_due_date,
            project.peer_review_due_date,
        )

        result = import_course_repository_curriculum(
            CurriculumImportCommand(
                source=source,
                source_uuid=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
                source_stable_id="llm-zoomcamp",
                repository_owner="DataTalksClub",
                repository_name="llm-zoomcamp",
                repository_branch="main",
                commit_sha=COMMIT_SHA,
                preserve_existing_records=True,
            )
        )

        cohort.refresh_from_db()
        homework.refresh_from_db()
        project.refresh_from_db()
        campaign.refresh_from_db()
        self.assertEqual(cohort.curriculum_format, CurriculumFormat.MODULES)
        self.assertEqual((cohort.start_date, cohort.end_date), (old_start, old_end))
        self.assertTrue(cohort.finished)
        self.assertTrue(cohort.first_homework_scored)
        self.assertEqual(cohort.min_projects_to_pass, 2)
        self.assertEqual(cohort.project_passing_score, 7)
        self.assertEqual(cohort.registration_url, "https://example.com/llm-register")
        self.assertEqual(
            (
                homework.slug,
                homework.title,
                homework.description,
                homework.instructions_markdown,
                homework.instructions_url,
                homework.due_date,
                homework.state,
            ),
            old_homework,
        )
        self.assertEqual(
            homework.source_content_id,
            UUID("51111111-1111-4111-8111-111111111111"),
        )
        self.assertEqual(homework.source_path, "cohorts/2026/01-agentic-rag/homework.yaml")
        self.assertEqual(homework.source_commit_sha, COMMIT_SHA)
        self.assertRegex(homework.source_checksum or "", r"^[0-9a-f]{64}$")
        self.assertEqual(
            (
                project.title,
                project.state,
                project.submission_due_date,
                project.peer_review_due_date,
            ),
            old_project,
        )
        self.assertEqual(campaign.current_course_id, cohort.pk)
        self.assertEqual(result.counts["modules"], 1)
        self.assertEqual(result.counts["homeworks"], 1)
        self.assertTrue(Module.objects.filter(cohort=cohort, terminal_homework=homework).exists())
        self.assertTrue(CurriculumFlowItem.objects.filter(cohort=cohort, project=project).exists())

        legacy = Cohort.objects.create(
            course=course,
            slug="llm-zoomcamp-2025",
            identifier="2025",
            year=2025,
            title="Legacy LLM cohort",
            description="Legacy cohort",
            curriculum_format=CurriculumFormat.LEGACY,
        )
        legacy_homework = Homework.objects.create(
            course=legacy,
            slug="legacy-homework",
            title="Legacy homework",
            due_date=timezone.now() + timedelta(days=5),
        )
        import_course_repository_curriculum(
            CurriculumImportCommand(
                source=replace(source, commit_sha="d" * 40),
                source_uuid=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
                source_stable_id="llm-zoomcamp",
                repository_owner="DataTalksClub",
                repository_name="llm-zoomcamp",
                repository_branch="main",
                commit_sha="d" * 40,
                preserve_existing_records=True,
            )
        )
        legacy.refresh_from_db()
        self.assertEqual(legacy.curriculum_format, CurriculumFormat.LEGACY)
        self.assertTrue(Homework.objects.filter(pk=legacy_homework.pk).exists())

    def test_adoption_replays_without_duplicate_rows(self):
        _course, cohort, _homework, _project, _campaign = self.make_existing_target()
        source = target_source_graph(
            fixture_source(),
            source_stable_id="llm-zoomcamp",
            cohort_identifier="2026",
            homework_slug_overrides={
                "cohorts/2026/01-agentic-rag/homework.yaml": "existing-homework"
            },
        )
        command = CurriculumImportCommand(
            source=source,
            source_uuid=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
            source_stable_id="llm-zoomcamp",
            repository_owner="DataTalksClub",
            repository_name="llm-zoomcamp",
            repository_branch="main",
            commit_sha=COMMIT_SHA,
            preserve_existing_records=True,
        )
        first = import_course_repository_curriculum(command)
        module_count = Module.objects.filter(cohort=cohort).count()
        unit_count = Unit.objects.filter(module__cohort=cohort).count()
        second = import_course_repository_curriculum(command)

        self.assertFalse(first.replayed)
        self.assertTrue(second.replayed)
        self.assertEqual(Module.objects.filter(cohort=cohort).count(), module_count)
        self.assertEqual(Unit.objects.filter(module__cohort=cohort).count(), unit_count)


class ReviewedCourseModuleRenderingTests(TestCase):
    def test_three_reviewed_cohorts_use_the_module_flow_rendering(self):
        for source_stable_id in TARGET_COHORTS:
            with self.subTest(source_stable_id=source_stable_id):
                family = Course.objects.create(
                    slug=f"{source_stable_id}-family",
                    title=f"{source_stable_id} family",
                )
                cohort = Cohort.objects.create(
                    course=family,
                    slug=f"{source_stable_id}-2026",
                    identifier="2026",
                    year=2026,
                    title=f"{source_stable_id} 2026",
                    description="A reviewed module cohort.",
                    curriculum_format=CurriculumFormat.MODULES,
                )
                homework = Homework.objects.create(
                    course=cohort,
                    slug="homework-01",
                    title="Module homework",
                    due_date=timezone.now() + timedelta(days=3),
                    state=HomeworkState.OPEN.value,
                )
                module = Module.objects.create(
                    cohort=cohort,
                    position=0,
                    slug="module-01",
                    title="First module",
                    terminal_homework=homework,
                )
                Unit.objects.create(
                    module=module,
                    position=0,
                    slug="lesson-01",
                    title="First lesson",
                )
                CurriculumFlowItem.objects.create(cohort=cohort, position=0, module=module)

                response = self.client.get(
                    reverse(
                        "course",
                        kwargs={
                            "course_slug": family.slug,
                            "cohort_year": cohort.year,
                        },
                    )
                )

                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["is_module_curriculum"])
                self.assertContains(response, 'id="curriculum-flow-heading"')
                self.assertContains(response, "First module")
                self.assertNotContains(response, 'id="projects-heading"')
