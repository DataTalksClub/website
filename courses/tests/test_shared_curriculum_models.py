"""Shared current curriculum models, constraints, and backfill service (W2)."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction as db_transaction
from django.test import TestCase
from django.utils import timezone

from courses.models import (
    Cohort,
    CohortSharedModule,
    Course,
    CurriculumFormat,
    CurriculumSource,
    DeliveryMode,
    Enrollment,
    Homework,
    Module,
    SharedCurriculum,
    SharedLesson,
    SharedLessonReadState,
    SharedModule,
    Unit,
    UnitReadState,
)
from courses.services.migrate_shared_curriculum import (
    backfill_shared_curriculum,
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


class SharedCurriculumModelTests(TestCase):
    def make_course(self, suffix: str) -> Course:
        return Course.objects.create(
            slug=f"shared-family-{suffix}",
            title=f"Shared Family {suffix}",
        )

    def make_cohort(
        self,
        suffix: str,
        *,
        course: Course | None = None,
        curriculum_format: str = CurriculumFormat.LEGACY,
        delivery_mode: str = DeliveryMode.LIVE,
        curriculum_source: str = CurriculumSource.CURRENT,
    ) -> Cohort:
        return Cohort.objects.create(
            course=course or self.make_course(suffix),
            slug=f"shared-cohort-{suffix}",
            title=f"Shared Cohort {suffix}",
            description="Shared model test cohort.",
            curriculum_format=curriculum_format,
            delivery_mode=delivery_mode,
            curriculum_source=curriculum_source,
        )

    def make_shared_graph(self, course: Course) -> tuple[SharedCurriculum, SharedModule, SharedLesson]:
        shared = SharedCurriculum.objects.create(
            course=course,
            parser_version="course-repository-v2",
            **provenance("11111111-1111-4111-8111-111111111111", "course.yaml"),
        )
        module = SharedModule.objects.create(
            curriculum=shared,
            position=0,
            slug="01-agentic-rag",
            title="Agentic RAG",
            **provenance("22222222-2222-4222-8222-222222222222", "01-agentic-rag/module.yaml"),
        )
        lesson = SharedLesson.objects.create(
            module=module,
            position=0,
            slug="01-lesson",
            title="Introduction",
            **provenance("33333333-3333-4333-8333-333333333333", "01-agentic-rag/01-lesson.md"),
        )
        return shared, module, lesson

    def test_shared_graph_defaults_and_retention_state(self) -> None:
        course = self.make_course("defaults")
        shared, module, lesson = self.make_shared_graph(course)

        self.assertTrue(module.published)
        self.assertIsNone(module.retired_at)
        self.assertTrue(lesson.published)
        self.assertEqual(module.curriculum, shared)
        self.assertEqual(shared.course, course)
        # OneToOne: a course owns exactly one current graph.
        with self.assertRaises(IntegrityError), db_transaction.atomic():
            SharedCurriculum.objects.create(
                course=course,
                parser_version="course-repository-v2",
                **provenance("11111111-1111-4111-8111-111111111112", "course.yaml"),
            )

    def test_duplicate_module_slug_position_and_source_id_are_rejected(self) -> None:
        course = self.make_course("dupes")
        shared, module, _ = self.make_shared_graph(course)
        with self.assertRaises(IntegrityError), db_transaction.atomic():
            SharedModule.objects.create(
                curriculum=shared,
                position=5,
                slug="01-agentic-rag",
                title="Duplicate slug",
            )
        with self.assertRaises(IntegrityError), db_transaction.atomic():
            SharedModule.objects.create(
                curriculum=shared,
                position=0,
                slug="02-other",
                title="Duplicate position",
            )
        with self.assertRaises(IntegrityError), db_transaction.atomic():
            SharedModule.objects.create(
                curriculum=shared,
                position=7,
                slug="03-again",
                title="Duplicate source id",
                **provenance("22222222-2222-4222-8222-222222222222", "01-agentic-rag/module.yaml"),
            )
        with self.assertRaises(IntegrityError), db_transaction.atomic():
            SharedLesson.objects.create(
                module=module,
                position=3,
                slug="01-lesson",
                title="Duplicate lesson slug",
            )
        with self.assertRaises(IntegrityError), db_transaction.atomic():
            SharedCurriculum.objects.create(
                course=course,
                parser_version="course-repository-v2",
                **provenance("11111111-1111-4111-8111-111111111112", "course.yaml"),
            )

    def test_placement_rejects_cross_course_module(self) -> None:
        cohort = self.make_cohort("placement", curriculum_format=CurriculumFormat.SHARED)
        other_course = self.make_course("other")
        _, other_module, _ = self.make_shared_graph(other_course)
        cohort.shared_curriculum = SharedCurriculum.objects.get(course=other_course)
        cohort.curriculum_format = CurriculumFormat.SHARED
        cohort.save()

        placement = CohortSharedModule(cohort=cohort, shared_module=other_module, position=0)
        with self.assertRaises(ValidationError) as raised:
            placement.full_clean()
        self.assertIn("shared_module", raised.exception.message_dict)

    def test_placement_rejects_homework_from_another_cohort(self) -> None:
        course = self.make_course("binding")
        cohort = self.make_cohort(
            "binding-cohort", course=course, curriculum_format=CurriculumFormat.SHARED
        )
        shared, module, _ = self.make_shared_graph(course)
        cohort.shared_curriculum = shared
        cohort.save()
        stranger = self.make_cohort("stranger")
        stranger_homework = Homework.objects.create(
            course=stranger,
            slug="stranger-hw",
            title="Stranger homework",
            due_date=timezone.now() + timedelta(days=7),
        )

        placement = CohortSharedModule(
            cohort=cohort,
            shared_module=module,
            position=0,
            terminal_homework=stranger_homework,
        )
        with self.assertRaises(ValidationError) as raised:
            placement.full_clean()
        self.assertIn("terminal_homework", raised.exception.message_dict)

    def test_self_paced_placement_rejects_homework(self) -> None:
        course = self.make_course("selfpaced")
        cohort = self.make_cohort(
            "selfpaced-cohort",
            course=course,
            curriculum_format=CurriculumFormat.SHARED,
            delivery_mode=DeliveryMode.SELF_PACED,
        )
        shared, module, _ = self.make_shared_graph(course)
        cohort.shared_curriculum = shared
        cohort.save()
        homework = Homework.objects.create(
            course=cohort,
            slug="graded-hw",
            title="Graded homework",
            due_date=timezone.now() + timedelta(days=7),
        )

        placement = CohortSharedModule(
            cohort=cohort,
            shared_module=module,
            position=0,
            terminal_homework=homework,
        )
        with self.assertRaises(ValidationError) as raised:
            placement.full_clean()
        self.assertIn("terminal_homework", raised.exception.message_dict)

    def test_cohort_discriminator_combinations_are_validated(self) -> None:
        course = self.make_course("discriminators")
        cohort = self.make_cohort(
            "disc-cohort",
            course=course,
            curriculum_format=CurriculumFormat.SHARED,
        )
        # Shared format without the graph pointer fails before any write.
        with self.assertRaises(ValidationError) as raised:
            cohort.full_clean()
        self.assertIn("shared_curriculum", raised.exception.message_dict)

        shared, _, _ = self.make_shared_graph(course)
        cohort.shared_curriculum = shared
        cohort.curriculum_source = CurriculumSource.GITHUB_ARCHIVE
        with self.assertRaises(ValidationError) as raised:
            cohort.full_clean()
        self.assertIn("curriculum_source", raised.exception.message_dict)

        # Archive identity requires all three derived fields.
        cohort.curriculum_source = CurriculumSource.CURRENT
        cohort.full_clean()
        cohort.curriculum_format = CurriculumFormat.LEGACY
        cohort.shared_curriculum = None
        cohort.curriculum_source = CurriculumSource.GITHUB_ARCHIVE
        cohort.archive_notice_path = "cohorts/2025/README.md"
        cohort.archive_url = (
            "https://github.com/DataTalksClub/llm-zoomcamp/blob/"
            + SHA
            + "/cohorts/2025/README.md"
        )
        with self.assertRaises(ValidationError) as raised:
            cohort.full_clean()
        self.assertIn("archive_notice_path", raised.exception.message_dict)
        cohort.archive_commit_sha = SHA
        cohort.full_clean()
        cohort.save()
        self.assertEqual(
            Cohort.objects.get(pk=cohort.pk).curriculum_source,
            CurriculumSource.GITHUB_ARCHIVE,
        )

        # A current cohort must not carry archive identity fields.
        cohort.curriculum_source = CurriculumSource.CURRENT
        with self.assertRaises(ValidationError) as raised:
            cohort.full_clean()
        self.assertIn("archive_notice_path", raised.exception.message_dict)

    def test_existing_rows_default_live_and_current(self) -> None:
        cohort = self.make_cohort("defaults")
        self.assertEqual(cohort.delivery_mode, DeliveryMode.LIVE)
        self.assertEqual(cohort.curriculum_source, CurriculumSource.CURRENT)
        self.assertEqual(cohort.curriculum_format, CurriculumFormat.LEGACY)


class SharedCurriculumBackfillTests(TestCase):
    def make_v1_module_world(self) -> tuple[Course, Cohort, Cohort, list[Module]]:
        course = Course.objects.create(
            slug="llm-zoomcamp",
            title="LLM Zoomcamp",
            source_stable_id="llm-zoomcamp",
            **provenance("11111111-1111-4111-8111-111111111111", "course.yaml"),
        )
        cohort_a = Cohort.objects.create(
            course=course,
            slug="llm-zoomcamp-2026",
            title="LLM Zoomcamp 2026",
            description="live",
            curriculum_format=CurriculumFormat.MODULES,
        )
        cohort_b = Cohort.objects.create(
            course=course,
            slug="llm-zoomcamp-self-paced",
            title="LLM Zoomcamp Self-Paced",
            description="self paced",
            year=2027,
            curriculum_format=CurriculumFormat.MODULES,
            delivery_mode=DeliveryMode.SELF_PACED,
        )
        homework_by_cohort = {}
        for cohort in (cohort_a, cohort_b):
            homework_by_cohort[cohort.pk] = Homework.objects.create(
                course=cohort,
                slug="hw1",
                title="Homework 1",
                due_date=timezone.now() + timedelta(days=7),
            )
        modules: list[Module] = []
        for cohort in (cohort_a, cohort_b):
            module = Module.objects.create(
                cohort=cohort,
                position=0,
                slug="01-agentic-rag",
                title="Agentic RAG",
                terminal_homework=homework_by_cohort[cohort.pk],
                **provenance("22222222-2222-4222-8222-222222222222", "01-agentic-rag/module.yaml"),
            )
            Unit.objects.create(
                module=module,
                position=0,
                slug="01-lesson",
                title="Introduction",
                content_markdown="# Introduction\n",
                rendered_html="<h1>Introduction</h1>\n",
                **provenance("33333333-3333-4333-8333-333333333333", "01-agentic-rag/01-lesson.md"),
            )
            modules.append(module)
        return course, cohort_a, cohort_b, modules

    def test_dry_run_is_repeatable_and_changes_no_rows(self) -> None:
        course, _, _, _ = self.make_v1_module_world()

        first = backfill_shared_curriculum(course_slug=course.slug, apply=False)
        second = backfill_shared_curriculum(course_slug=course.slug, apply=False)

        self.assertTrue(first.dry_run)
        self.assertEqual(first.counts, second.counts)
        self.assertEqual(first.counts["shared_modules"], 1)
        self.assertEqual(first.counts["shared_lessons"], 1)
        self.assertEqual(first.counts["placements"], 2)
        self.assertEqual(SharedCurriculum.objects.count(), 0)
        self.assertEqual(SharedModule.objects.count(), 0)
        self.assertEqual(SharedLesson.objects.count(), 0)

    def test_apply_creates_one_graph_and_is_idempotent(self) -> None:
        course, cohort_a, cohort_b, _ = self.make_v1_module_world()

        first = backfill_shared_curriculum(course_slug=course.slug, apply=True)
        second = backfill_shared_curriculum(course_slug=course.slug, apply=True)

        self.assertEqual(first.counts["shared_modules"], 1)
        self.assertEqual(second.counts["shared_modules"], 0)
        self.assertEqual(second.counts["shared_lessons"], 0)
        self.assertEqual(second.counts["placements"], 0)
        self.assertEqual(SharedModule.objects.count(), 1)
        self.assertEqual(SharedLesson.objects.count(), 1)

        placements = CohortSharedModule.objects.order_by("cohort__slug")
        self.assertEqual(placements.count(), 2)
        self.assertEqual(
            {placement.cohort_id for placement in placements},
            {cohort_a.pk, cohort_b.pk},
        )
        # Both deliveries reference the same shared module identity...
        self.assertEqual(
            {placement.shared_module_id for placement in placements},
            {placements[0].shared_module_id},
        )
        # ...while each placement keeps its own cohort's terminal homework.
        for placement in placements:
            self.assertEqual(placement.terminal_homework.course_id, placement.cohort_id)

        # Old cohort-owned rows are untouched.
        self.assertEqual(Module.objects.count(), 2)
        self.assertEqual(Unit.objects.count(), 2)

    def test_earliest_read_timestamps_are_copied_per_stable_id(self) -> None:
        course, cohort_a, cohort_b, modules = self.make_v1_module_world()
        earlier = timezone.now() - timedelta(days=3)
        later = timezone.now() - timedelta(days=1)
        learner = Enrollment.objects.create(
            student=get_user_model().objects.create_user(username="reader"),
            course=cohort_a,
        )
        UnitReadState.objects.create(
            user=learner.student, unit=modules[0].units.first(), read_at=later
        )
        UnitReadState.objects.create(
            user=learner.student,
            unit=modules[1].units.first(),
            read_at=earlier,
        )

        backfill_shared_curriculum(course_slug=course.slug, apply=True)

        state = SharedLessonReadState.objects.get(
            shared_lesson__source_content_id="33333333-3333-4333-8333-333333333333",
            user=learner.student,
        )
        self.assertEqual(state.read_at, earlier)

        # Re-running never duplicates or regresses a read marker.
        backfill_shared_curriculum(course_slug=course.slug, apply=True)
        self.assertEqual(SharedLessonReadState.objects.count(), 1)

    def test_conflicting_stable_ids_stop_the_run_without_writes(self) -> None:
        course, _, _, modules = self.make_v1_module_world()
        conflicting = modules[1]
        conflicting.title = "A Different Title"
        conflicting.slug = "02-renamed"
        conflicting.source_path = "02-renamed/module.yaml"
        conflicting.save(update_fields=("title", "slug", "source_path"))

        dry = backfill_shared_curriculum(course_slug=course.slug, apply=False)
        self.assertEqual(len(dry.conflicts), 1)
        self.assertEqual(dry.conflicts[0]["kind"], "shared_module")

        # An apply run refuses the same way and changes no rows.
        applied = backfill_shared_curriculum(course_slug=course.slug, apply=True)
        self.assertEqual(applied.conflicts, dry.conflicts)
        self.assertIsNone(applied.shared_curriculum_id)
        self.assertEqual(SharedCurriculum.objects.count(), 0)
        self.assertEqual(SharedModule.objects.count(), 0)
        self.assertEqual(SharedLesson.objects.count(), 0)
        self.assertEqual(CohortSharedModule.objects.count(), 0)

    def test_empty_database_has_no_public_content(self) -> None:
        self.assertEqual(SharedCurriculum.objects.count(), 0)
        self.assertEqual(SharedModule.objects.count(), 0)
        self.assertEqual(SharedLesson.objects.count(), 0)
