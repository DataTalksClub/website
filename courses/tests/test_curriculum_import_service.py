from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import UUID

from django.test import TestCase
from django.utils import timezone

from content_sync.course_repository import (
    CohortSource,
    CourseRepositorySource,
    ModuleFlowSource,
    parse_course_repository,
)
from courses.models import (
    Answer,
    AnswerTypes,
    Cohort,
    Course,
    CourseCurriculumImportRun,
    CurriculumFlowItem,
    CurriculumFormat,
    Enrollment,
    Homework,
    HomeworkState,
    Module,
    Project,
    ProjectCriteriaAssignment,
    Question,
    QuestionTypes,
    ReviewCriteria,
    ReviewCriteriaTypes,
    Submission,
    Unit,
    User,
)
from courses.services.curriculum_import import (
    CurriculumImportCommand,
    CurriculumImportError,
    import_course_repository_curriculum,
)

FIXTURE_ROOT = (
    Path(__file__).parents[2]
    / "content_sync"
    / "tests"
    / "fixtures"
    / "course_repository"
    / "llm_zoomcamp_2026"
)
FIRST_COMMIT = "a" * 40
SECOND_COMMIT = "b" * 40
SOURCE_UUID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


def fixture_source(*, commit_sha: str = FIRST_COMMIT) -> CourseRepositorySource:
    snapshot = {
        path.relative_to(FIXTURE_ROOT).as_posix(): path.read_bytes()
        for path in FIXTURE_ROOT.rglob("*")
        if path.is_file()
    }
    return parse_course_repository(snapshot, commit_sha=commit_sha)


def explicit_legacy_source() -> CourseRepositorySource:
    source = fixture_source()
    explicit_legacy = next(
        cohort
        for cohort in source.cohorts
        if cohort.format == "legacy" and not cohort.is_implicit_legacy
    )
    return replace(
        source,
        cohorts=(explicit_legacy,),
        modules=(),
        homeworks=(),
    )


def import_command(
    source: CourseRepositorySource,
    *,
    commit_sha: str = FIRST_COMMIT,
    source_uuid: UUID = SOURCE_UUID,
) -> CurriculumImportCommand:
    return CurriculumImportCommand(
        source=source,
        source_uuid=source_uuid,
        source_stable_id="llm-zoomcamp",
        repository_owner="DataTalksClub",
        repository_name="llm-zoomcamp",
        repository_branch="main",
        commit_sha=commit_sha,
    )


class CurriculumImportServiceTests(TestCase):
    def create_adoption_target(self) -> tuple[Course, Cohort, Project]:
        course = Course.objects.create(
            slug="llm-zoomcamp",
            title="DB-managed LLM course",
        )
        cohort = Cohort.objects.create(
            course=course,
            slug="llm-zoomcamp-2026",
            identifier="2026",
            year=2026,
            title="DB-managed LLM cohort",
            description="Existing cohort",
            min_projects_to_pass=2,
            project_passing_score=7,
            registration_url="https://example.com/register",
        )
        now = timezone.now()
        project = Project.objects.create(
            course=cohort,
            slug="project-01",
            title="Existing capstone",
            submission_due_date=now + timedelta(days=30),
            peer_review_due_date=now + timedelta(days=37),
        )
        return course, cohort, project

    def import_fixture_with_project(self):
        course, cohort, project = self.create_adoption_target()
        result = import_course_repository_curriculum(import_command(fixture_source()))
        course.refresh_from_db()
        cohort.refresh_from_db()
        project.refresh_from_db()
        return result, course, cohort, project

    def test_creates_new_course_and_explicit_cohort_from_source_metadata(self):
        result = import_course_repository_curriculum(import_command(explicit_legacy_source()))

        self.assertFalse(result.replayed)
        self.assertEqual(result.course.slug, "llm-zoomcamp")
        self.assertEqual(result.course.source_stable_id, "llm-zoomcamp")
        self.assertEqual(
            result.course.source_content_id, UUID("11111111-1111-4111-8111-111111111111")
        )
        cohort = Cohort.objects.get(course=result.course)
        self.assertEqual(cohort.identifier, "2025")
        self.assertEqual(cohort.curriculum_format, CurriculumFormat.LEGACY)
        self.assertTrue(cohort.visible)
        self.assertEqual(cohort.source_path, "cohorts/2025/cohort.yaml")
        self.assertEqual(result.counts["cohorts"], 1)
        self.assertEqual(result.run.state, CourseCurriculumImportRun.State.SUCCEEDED)
        self.assertRegex(result.run.manifest_checksum or "", r"^[0-9a-f]{64}$")

    def test_explicit_legacy_metadata_does_not_import_or_replace_curriculum(self):
        course = Course.objects.create(slug="llm-zoomcamp", title="Existing")
        cohort = Cohort.objects.create(
            course=course,
            slug="llm-zoomcamp-2025",
            identifier="2025",
            year=2025,
            title="Existing 2025",
            description="Existing",
        )
        homework = Homework.objects.create(
            course=cohort,
            slug="legacy-homework",
            title="Legacy homework",
            due_date=timezone.now() + timedelta(days=7),
        )
        project = Project.objects.create(
            course=cohort,
            slug="legacy-project",
            title="Legacy project",
            submission_due_date=timezone.now() + timedelta(days=8),
            peer_review_due_date=timezone.now() + timedelta(days=9),
        )

        result = import_course_repository_curriculum(import_command(explicit_legacy_source()))

        cohort.refresh_from_db()
        self.assertEqual(cohort.curriculum_format, CurriculumFormat.LEGACY)
        self.assertEqual(cohort.title, "LLM Zoomcamp 2025")
        self.assertTrue(Homework.objects.filter(pk=homework.pk).exists())
        self.assertTrue(Project.objects.filter(pk=project.pk).exists())
        self.assertEqual(Module.objects.filter(cohort=cohort).count(), 0)
        self.assertEqual(result.counts["homeworks"], 0)

    def test_llm_fixture_materializes_modules_units_homework_and_questions(self):
        result, course, cohort, project = self.import_fixture_with_project()

        self.assertEqual(Cohort.objects.filter(course=course).count(), 2)
        self.assertFalse(Cohort.objects.filter(course=course, identifier="2024").exists())
        self.assertEqual(cohort.curriculum_format, CurriculumFormat.MODULES)
        module = Module.objects.get(cohort=cohort)
        self.assertEqual(module.terminal_homework.slug, "hw1")
        self.assertEqual(module.source_content_id, UUID("21111111-1111-4111-8111-111111111111"))
        units = list(Unit.objects.filter(module=module))
        self.assertEqual([unit.slug for unit in units], ["01-intro", "02-environment"])
        self.assertIn("The first lesson in the Agentic RAG", units[0].content_markdown)
        self.assertIn("<", units[0].rendered_html)
        # The lesson frontmatter never reaches ``content_markdown``, so the
        # projection is the only place its video and code files survive.
        self.assertEqual(units[0].video_url, "https://www.youtube.com/watch?v=fixture-intro")
        self.assertEqual(
            units[0].code_sources,
            [
                {
                    "label": "notebook.ipynb",
                    "source_path": "cohorts/2026/01-agentic-rag/code/notebook.ipynb",
                }
            ],
        )
        self.assertEqual(units[1].video_url, "")
        self.assertEqual(units[1].code_sources, [])

        homework = module.terminal_homework
        self.assertIn("working through every unit", homework.instructions_markdown)
        self.assertEqual(homework.state, HomeworkState.OPEN.value)
        questions = list(Question.objects.filter(homework=homework).order_by("pk"))
        self.assertEqual(
            [question.question_type for question in questions],
            [QuestionTypes.MULTIPLE_CHOICE.value, QuestionTypes.FREE_FORM_LONG.value],
        )
        self.assertEqual(questions[0].source_option_ids, ["pages-24", "pages-72"])
        self.assertEqual(questions[0].possible_answers, "24\n72")
        self.assertIsNotNone(questions[0].answer_envelope)
        self.assertIsNone(questions[0].correct_answer)
        self.assertEqual(questions[1].answer_type, AnswerTypes.ANY.value)
        self.assertIsNone(questions[1].answer_envelope)
        self.assertTrue(Project.objects.filter(pk=project.pk).exists())
        self.assertEqual(result.counts["modules"], 1)
        self.assertEqual(result.counts["units"], 2)
        self.assertEqual(result.counts["questions"], 2)

    def test_shared_module_source_is_isolated_per_cohort_and_upserts_by_source_id(self):
        _course, cohort_2026, _project = self.create_adoption_target()
        source = fixture_source()
        modules_cohort = next(cohort for cohort in source.cohorts if cohort.format == "modules")
        module_flow = next(
            item for item in modules_cohort.flow if isinstance(item, ModuleFlowSource)
        )
        second_homework = replace(
            module_flow.homework,
            content_id="53333333-3333-4333-8333-333333333333",
            source_path="cohorts/spring-2027/01-agentic-rag/homework.yaml",
            due_at=module_flow.homework.due_at + timedelta(days=365),
        )
        second_cohort = replace(
            modules_cohort,
            content_id="43333333-3333-4333-8333-333333333333",
            source_path="cohorts/spring-2027/cohort.yaml",
            identifier="spring-2027",
            legacy_slug="llm-zoomcamp-spring-2027",
            year=2027,
            title="LLM Zoomcamp Spring 2027",
            start_date=modules_cohort.start_date + timedelta(days=365),
            end_date=modules_cohort.end_date + timedelta(days=365),
            flow=(ModuleFlowSource(module=module_flow.module, homework=second_homework),),
        )
        source = replace(
            source,
            cohorts=(modules_cohort, second_cohort),
            homeworks=(module_flow.homework, second_homework),
        )

        first = import_course_repository_curriculum(import_command(source))
        cohort_2027 = Cohort.objects.get(course=first.course, identifier="spring-2027")
        modules = list(
            Module.objects.filter(cohort__in=(cohort_2026, cohort_2027)).order_by("cohort_id")
        )
        self.assertEqual(len(modules), 2)
        self.assertEqual(modules[0].source_content_id, modules[1].source_content_id)
        original_module_pks = {module.cohort_id: module.pk for module in modules}
        original_unit_pks = {
            unit.module.cohort_id: unit.pk
            for unit in Unit.objects.filter(slug="01-intro").select_related("module")
        }

        updated_unit = replace(
            module_flow.module.units[0],
            title="Updated shared introduction",
            markdown="# Updated shared introduction\n",
        )
        updated_module = replace(
            module_flow.module,
            units=(updated_unit, *module_flow.module.units[1:]),
        )
        updated_cohorts: list[CohortSource] = []
        for cohort_source in source.cohorts:
            flow = tuple(
                ModuleFlowSource(module=updated_module, homework=item.homework)
                if isinstance(item, ModuleFlowSource)
                else item
                for item in cohort_source.flow
            )
            updated_cohorts.append(replace(cohort_source, flow=flow))
        updated_source = replace(
            source,
            commit_sha=SECOND_COMMIT,
            cohorts=tuple(updated_cohorts),
            modules=(updated_module,),
        )

        import_course_repository_curriculum(
            import_command(updated_source, commit_sha=SECOND_COMMIT)
        )

        for module in Module.objects.filter(cohort__in=(cohort_2026, cohort_2027)):
            self.assertEqual(module.pk, original_module_pks[module.cohort_id])
            unit = module.units.get(slug="01-intro")
            self.assertEqual(unit.pk, original_unit_pks[module.cohort_id])
            self.assertEqual(unit.title, "Updated shared introduction")

    def test_exact_source_commit_and_parser_replay_is_a_no_op(self):
        source = explicit_legacy_source()
        first = import_course_repository_curriculum(import_command(source))
        course_pk = first.course.pk
        cohort_pk = first.cohorts[0].pk

        second = import_course_repository_curriculum(import_command(source))

        self.assertTrue(second.replayed)
        self.assertEqual(second.run.pk, first.run.pk)
        self.assertEqual(second.course.pk, course_pk)
        self.assertEqual(second.cohorts[0].pk, cohort_pk)
        self.assertEqual(CourseCurriculumImportRun.objects.count(), 1)
        self.assertEqual(Course.objects.count(), 1)
        self.assertEqual(Cohort.objects.count(), 1)

    def test_project_is_placed_in_exact_source_flow_order(self):
        _result, _course, cohort, project = self.import_fixture_with_project()

        flow = list(CurriculumFlowItem.objects.filter(cohort=cohort))
        self.assertEqual([item.position for item in flow], [0, 1])
        self.assertIsNotNone(flow[0].module_id)
        self.assertEqual(flow[1].project_id, project.pk)
        self.assertEqual(Project.objects.filter(course=cohort).count(), 1)

    def test_missing_project_rejects_atomically_with_redacted_diagnostic(self):
        with self.assertRaises(CurriculumImportError) as raised:
            import_course_repository_curriculum(import_command(fixture_source()))

        self.assertEqual(raised.exception.code, "project_reference_missing")
        self.assertFalse(Course.objects.filter(slug="llm-zoomcamp").exists())
        self.assertEqual(Cohort.objects.count(), 0)
        run = CourseCurriculumImportRun.objects.get()
        self.assertEqual(run.state, CourseCurriculumImportRun.State.REJECTED)
        self.assertEqual(
            run.diagnostics,
            [
                {
                    "code": "project_reference_missing",
                    "source_path": "cohorts/2026/cohort.yaml",
                    "pointer": "/flow/1/project",
                }
            ],
        )
        self.assertNotIn("project-01", str(run.diagnostics))

    def test_import_preserves_db_owned_cohort_homework_project_and_criteria_fields(self):
        _result, _course, cohort, project = self.import_fixture_with_project()
        criterion = ReviewCriteria.objects.create(
            course=cohort,
            description="Keep this criterion",
            options=[{"criteria": "Good", "score": 1}],
            review_criteria_type=ReviewCriteriaTypes.RADIO_BUTTONS.value,
        )
        assignment = ProjectCriteriaAssignment.objects.create(
            project=project,
            criteria=criterion,
            position=0,
        )
        homework = Homework.objects.get(course=cohort, slug="hw1")
        homework.state = HomeworkState.SCORED.value
        homework.description = "Operator-owned note"
        homework.instructions_url = "https://example.com/operator-instructions"
        homework.save()
        cohort.finished = True
        cohort.first_homework_scored = True
        cohort.min_projects_to_pass = 2
        cohort.project_passing_score = 7
        cohort.save()
        project.state = "PR"
        project.title = "Operator-owned project title"
        project.save()

        changed_source = fixture_source(commit_sha=SECOND_COMMIT)
        changed_source = replace(
            changed_source,
            course=replace(changed_source.course, title="Updated source title"),
        )
        import_course_repository_curriculum(
            import_command(changed_source, commit_sha=SECOND_COMMIT)
        )

        cohort.refresh_from_db()
        homework.refresh_from_db()
        project.refresh_from_db()
        self.assertTrue(cohort.finished)
        self.assertTrue(cohort.first_homework_scored)
        self.assertEqual(cohort.min_projects_to_pass, 2)
        self.assertEqual(cohort.project_passing_score, 7)
        self.assertEqual(homework.state, HomeworkState.SCORED.value)
        self.assertEqual(homework.description, "Operator-owned note")
        self.assertEqual(homework.instructions_url, "https://example.com/operator-instructions")
        self.assertEqual(project.title, "Operator-owned project title")
        self.assertEqual(project.state, "PR")
        self.assertTrue(ProjectCriteriaAssignment.objects.filter(pk=assignment.pk).exists())
        self.assertTrue(ReviewCriteria.objects.filter(pk=criterion.pk).exists())

    def test_history_breaking_question_change_rolls_back_all_source_updates(self):
        _result, course, cohort, _project = self.import_fixture_with_project()
        homework = Homework.objects.get(course=cohort, slug="hw1")
        question = Question.objects.get(homework=homework, source_question_id="lesson-page-count")
        user = User.objects.create_user(username="learner")
        enrollment = Enrollment.objects.create(student=user, course=cohort)
        submission = Submission.objects.create(
            homework=homework,
            student=user,
            enrollment=enrollment,
        )
        Answer.objects.create(submission=submission, question=question, answer_text="1")

        source = fixture_source(commit_sha=SECOND_COMMIT)
        modules_cohort = next(item for item in source.cohorts if item.format == "modules")
        module_flow = next(
            item for item in modules_cohort.flow if isinstance(item, ModuleFlowSource)
        )
        changed_question = replace(module_flow.homework.questions[0], points=99)
        changed_homework = replace(
            module_flow.homework,
            questions=(changed_question, *module_flow.homework.questions[1:]),
        )
        changed_flow = tuple(
            ModuleFlowSource(module=item.module, homework=changed_homework)
            if isinstance(item, ModuleFlowSource)
            else item
            for item in modules_cohort.flow
        )
        changed_cohort = replace(modules_cohort, flow=changed_flow)
        changed_source = replace(
            source,
            course=replace(source.course, title="Must roll back"),
            cohorts=tuple(
                changed_cohort if item.content_id == changed_cohort.content_id else item
                for item in source.cohorts
            ),
            homeworks=(changed_homework,),
        )

        with self.assertRaises(CurriculumImportError) as raised:
            import_course_repository_curriculum(
                import_command(changed_source, commit_sha=SECOND_COMMIT)
            )

        self.assertEqual(raised.exception.code, "protected_question_change")
        course.refresh_from_db()
        question.refresh_from_db()
        self.assertEqual(course.title, "LLM Zoomcamp")
        self.assertEqual(question.scores_for_correct_answer, 1)
        rejected = CourseCurriculumImportRun.objects.get(commit_sha=SECOND_COMMIT)
        self.assertEqual(rejected.state, CourseCurriculumImportRun.State.REJECTED)
