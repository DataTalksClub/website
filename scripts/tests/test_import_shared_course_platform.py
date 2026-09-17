"""The P6 course-platform import: rehearsal, replay, and the gap ledger.

The mapping decisions these tests lock in are the decision record in
``_docs/architecture/course-platform-shared-apps-mapping.md``; each test names
the decision it pins.  The fixture graph is deliberately small but touches
every family the import copies, including the skipped and gap-counted rows, so
the count-equality verification runs against a graph that is wrong in exactly
the ways the decisions say it may be.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from scripts.prod.import_shared_course_platform import (
    PROVENANCE_NAMESPACE,
    CountMismatch,
    SelfPacedCohortNotUnique,
    SharedBackfillRequired,
    UnknownDeliveryMode,
    _package_carries,
    import_course_platform,
)

_SHA = "a" * 40
_CHECKSUM = "b" * 64
_CERT_URL = "https://certificates.datatalks.club/p6/learner/2026"
_PAST = datetime(2026, 1, 15, 10, 30, tzinfo=UTC)


def _import_family_graph() -> None:
    """One course family touching every family, gap and skip the import has."""

    from courses import models as site

    learner = get_user_model().objects.create_user(username="p6-learner")
    peer = get_user_model().objects.create_user(username="p6-peer")

    family = site.Course.objects.create(
        slug="p6-course",
        title="P6 Course",
        description="# P6 Course\n\nA body paragraph.",
        github_repo_url="https://github.com/DataTalksClub/p6-course",
        docs_url="https://docs.example.com/p6",
        faq_document_url="https://docs.example.com/p6/faq",
        social_media_hashtag="#p6course",
        visible=True,
        source_content_id="p6-course",
        source_stable_id="p6-course-stable",
        source_path="course.yaml",
        source_commit_sha=_SHA,
        source_checksum=_CHECKSUM,
    )
    site.Course.objects.create(slug="p6-hidden", title="P6 Hidden", visible=False)

    shared = site.SharedCurriculum.objects.create(course=family, parser_version="p6-test")
    published_module = site.SharedModule.objects.create(
        curriculum=shared,
        position=1,
        slug="intro",
        title="Intro",
        overview_markdown="Module overview text.",
        overview_rendered_html="<p>Module overview text.</p>\n",
        summary="One module, three lessons.",
    )
    site.SharedModule.objects.create(
        curriculum=shared,
        position=2,
        slug="extra",
        title="Extra",
        published=False,
    )
    coded_lesson = site.SharedLesson.objects.create(
        module=published_module,
        position=1,
        slug="welcome",
        title="Welcome",
        content_markdown="Lesson body.",
        rendered_html="<p>Lesson body.</p>\n",
        video_url="https://video.example.com/welcome",
        code_sources=[{"label": "notebook", "source_path": "code/welcome.ipynb"}],
    )
    site.SharedCurriculumAsset.objects.create(
        lesson=coded_lesson,
        public_path="p6/welcome/notebook.ipynb",
        storage_key="p6/welcome/notebook@aabb.ipynb",
        content_type="application/x-ipynb+json",
        byte_size=12,
        source_path="code/welcome.ipynb",
        source_checksum=_CHECKSUM,
        source_commit_sha=_SHA,
    )
    site.SharedLesson.objects.create(
        module=published_module,
        position=2,
        slug="retired",
        title="Retired",
        content_markdown="Old body.",
        retired_at=timezone.now(),
    )
    site.SharedLesson.objects.create(
        module=published_module,
        position=3,
        slug="draft-lesson",
        title="Draft",
        content_markdown="Draft body.",
        published=False,
    )
    hidden_module = site.SharedModule.objects.get(slug="extra")
    site.SharedLesson.objects.create(
        module=hidden_module,
        position=1,
        slug="orphan",
        title="Orphan",
        content_markdown="Under an unpublished module.",
    )

    cohort = site.Cohort.objects.create(
        slug="p6-course-2026",
        course=family,
        identifier="2026",
        year=2026,
        title="P6 Course 2026",
        curriculum_format=site.CurriculumFormat.SHARED,
        delivery_mode=site.DeliveryMode.LIVE,
        start_date=datetime(2026, 2, 1).date(),
        end_date=datetime(2026, 5, 1).date(),
        registration_url="https://example.com/register",
        social_media_hashtag="#p62026",
        first_homework_scored=True,
        min_projects_to_pass=1,
        project_passing_score=5,
    )
    site.Cohort.objects.create(
        slug="p6-course-self-paced",
        course=family,
        identifier="self-paced",
        year=2025,
        title="P6 Course Self-paced",
        curriculum_format=site.CurriculumFormat.SHARED,
        delivery_mode=site.DeliveryMode.SELF_PACED,
        promo_summary="Start any time.",
    )
    site.SharedLessonReadState.objects.create(user=learner, shared_lesson=coded_lesson)

    homework = site.Homework.objects.create(
        slug="hw1",
        course=cohort,
        title="Homework 1",
        description="First homework.",
        due_date=datetime(2026, 2, 15, tzinfo=UTC),
    )
    site.CohortSharedModule.objects.create(
        cohort=cohort,
        shared_module=published_module,
        position=1,
        terminal_homework=homework,
    )
    site.CohortSharedModule.objects.create(
        cohort=cohort,
        shared_module=hidden_module,
        position=2,
    )
    provenanced_question = site.Question.objects.create(
        homework=homework,
        text="What is 2+2?",
        question_type=site.QuestionTypes.MULTIPLE_CHOICE.value,
        answer_type=site.AnswerTypes.EXACT_STRING.value,
        possible_answers="3\n4\n5",
        correct_answer="2",
        source_question_id="q1",
        source_content_id="p6-course/homework-1/question-1",
        source_path="homeworks/hw1.md",
        source_commit_sha=_SHA,
        source_checksum=_CHECKSUM,
        scores_for_correct_answer=2,
    )
    free_question = site.Question.objects.create(
        homework=homework,
        text="Free reflection",
        question_type=site.QuestionTypes.FREE_FORM.value,
    )
    submission = site.Submission.objects.create(
        homework=homework,
        student=learner,
        enrollment=site.Enrollment.objects.create(student=learner, course=cohort),
        submitted_at=_PAST,
        questions_score=2,
        total_score=7,
    )
    site.Enrollment.objects.create(student=peer, course=cohort)
    site.Enrollment.objects.filter(student=learner, course=cohort).update(
        certificate_name="Learner Name",
        certificate_url=_CERT_URL,
        enrollment_date=_PAST,
        display_name="learner-display",
        total_score=7,
    )
    site.Answer.objects.create(
        submission=submission,
        question=provenanced_question,
        answer_text="4",
        is_correct=True,
    )
    site.Answer.objects.create(
        submission=submission,
        question=free_question,
        answer_text="Thinking.",
    )
    site.HomeworkStatistics.objects.create(
        homework=homework,
        total_submissions=1,
        max_total_score=7,
    )

    criteria = site.ReviewCriteria.objects.create(
        course=cohort,
        description="Code quality",
        options=[
            {"criteria": "Poor", "score": 0},
            {"criteria": "Good", "score": 1},
        ],
        review_criteria_type=site.ReviewCriteriaTypes.RADIO_BUTTONS.value,
    )
    project = site.Project.objects.create(
        course=cohort,
        slug="final",
        title="Final project",
        submission_due_date=datetime(2026, 4, 1, tzinfo=UTC),
        peer_review_due_date=datetime(2026, 4, 8, tzinfo=UTC),
    )
    site.ProjectCriteriaAssignment.objects.create(project=project, criteria=criteria, position=0)
    learner_submission = site.ProjectSubmission.objects.create(
        project=project,
        student=learner,
        enrollment=site.Enrollment.objects.get(student=learner, course=cohort),
        github_link="https://github.com/learner/p6-project",
        commit_id="c" * 40,
        submitted_at=_PAST,
        total_score=9,
        passed=True,
    )
    peer_submission = site.ProjectSubmission.objects.create(
        project=project,
        student=peer,
        enrollment=site.Enrollment.objects.get(student=peer, course=cohort),
        github_link="https://github.com/peer/p6-project",
        commit_id="d" * 40,
    )
    vote = site.ProjectVote.objects.create(
        submission=peer_submission,
        voter=learner,
    )
    site.ProjectVote.objects.filter(pk=vote.pk).update(created_at=_PAST)
    review = site.PeerReview.objects.create(
        submission_under_evaluation=peer_submission,
        reviewer=learner_submission,
        note_to_peer="Nice work.",
        state=site.PeerReviewState.SUBMITTED.value,
        submitted_at=_PAST,
    )
    site.CriteriaResponse.objects.create(review=review, criteria=criteria, answer="2")
    site.ProjectEvaluationScore.objects.create(
        submission=peer_submission, review_criteria=criteria, score=1
    )
    site.ProjectStatistics.objects.create(
        project=project,
        total_submissions=2,
        max_total_score=9,
    )
    site.LeaderboardComplaint.objects.create(
        enrollment=site.Enrollment.objects.get(student=learner, course=cohort),
        reporter=peer,
        issue_type=site.LeaderboardComplaint.IssueType.HOMEWORK,
        description="Score looks wrong.",
    )

    campaign = site.RegistrationCampaign.objects.create(
        slug="p6-campaign",
        title="Join P6 Course",
        edition_label="2026 cohort",
        current_course=cohort,
        marketing_markdown="Come learn.",
    )
    site.CourseRegistration.objects.create(
        campaign=campaign,
        course=cohort,
        user=learner,
        email="learner@example.com",
        name="Learner",
        country="Spain",
        region="EU",
        role=site.CourseRegistration.Role.OTHER,
    )
    stale_registration = site.CourseRegistration.objects.create(
        campaign=campaign,
        user=peer,
        email="peer@example.com",
        name="Peer",
        country="Spain",
        region="EU",
        role=site.CourseRegistration.Role.OTHER,
    )
    # A registration recorded before the campaign promoted 2026: the site
    # row names no cohort, and both save() implementations would now default
    # it -- so the stored null is written back the way the row really sits.
    site.CourseRegistration.objects.filter(pk=stale_registration.pk).update(course=None)

    flow_cohort = site.Cohort.objects.create(
        slug="p6-course-modules",
        course=family,
        identifier="modules-flow",
        year=2024,
        title="P6 Course Modules Flow",
        curriculum_format=site.CurriculumFormat.MODULES,
    )
    flow_project = site.Project.objects.create(
        course=flow_cohort,
        slug="flow-project",
        title="Flow project",
        submission_due_date=datetime(2026, 4, 1, tzinfo=UTC),
        peer_review_due_date=datetime(2026, 4, 8, tzinfo=UTC),
    )
    site.CurriculumFlowItem.objects.create(cohort=flow_cohort, position=1, project=flow_project)
    site.Testimonial.objects.create(
        placement=site.TestimonialPlacement.COURSE,
        course=family,
        name="Graduate",
        attribution="Data Engineer · Spain",
        quote="The course changed how I work.",
        published=True,
        position=1,
    )

    wrapped = site.WrappedStatistics.objects.create(
        year=2099,
        total_participants=2,
        total_enrollments=3,
    )
    site.UserWrappedStatistics.objects.create(
        wrapped=wrapped,
        user=learner,
        total_points=16,
    )


class ImportRunMixin:
    def run_import(self, *, apply: bool = True) -> dict:
        return import_course_platform(apply=apply)


class SharedCoursePlatformImportTests(ImportRunMixin, TestCase):
    """Rehearse the copy on a small site graph and lock in the decisions."""

    @classmethod
    def setUpTestData(cls) -> None:
        _import_family_graph()

    def test_a_dry_run_reports_but_writes_nothing(self) -> None:
        from community_base.coursework import models as cw
        from community_base.curriculum import models as cur

        report = self.run_import(apply=False)

        self.assertFalse(report["applied"])
        self.assertEqual(cur.Course.objects.count(), 0)
        self.assertEqual(cw.Homework.objects.count(), 0)
        self.assertTrue(report["verification"]["courses"]["equal"])

    def test_an_applied_run_lands_every_family_with_equal_counts(self) -> None:
        report = self.run_import(apply=True)

        self.assertTrue(report["applied"])
        for family, entry in report["verification"].items():
            self.assertTrue(
                entry["equal"],
                f"{family}: site {entry['site']} vs package {entry['package']}",
            )
        self.assertEqual(report["counts"]["courses"]["migrated"], 2)
        self.assertEqual(report["counts"]["cohorts"]["migrated"], 3)
        self.assertEqual(report["counts"]["enrollments"]["migrated"], 2)
        self.assertEqual(report["counts"]["certificates"]["migrated"], 1)

    def test_a_replay_creates_nothing_new(self) -> None:
        from community_base.coursework import models as cw
        from community_base.curriculum import models as cur

        self.run_import(apply=True)
        before = {model: model.objects.count() for model in (cur.Course, cw.Homework)}

        replay = self.run_import(apply=True)

        self.assertTrue(replay["applied"])
        self.assertEqual(replay["verification"]["courses"]["package"], before[cur.Course])
        self.assertEqual(replay["verification"]["homework"]["package"], before[cw.Homework])

    def test_the_course_maps_with_renames_and_status(self) -> None:
        """Decisions 5 and 10: renames land, site-only long-form stays behind."""

        from community_base.curriculum.models import Course

        self.run_import(apply=True)

        course = Course.objects.get(slug="p6-course")
        self.assertEqual(course.title, "P6 Course")
        self.assertEqual(course.faq_url, "https://docs.example.com/p6/faq")
        self.assertEqual(course.hashtag, "#p6course")
        self.assertEqual(course.status, "published")
        self.assertNotEqual(course.description_html, "")
        hidden = Course.objects.get(slug="p6-hidden")
        self.assertEqual(hidden.status, "draft")
        self.assertFalse(hidden.visible)

    def test_provenance_converts_through_the_fixed_namespace(self) -> None:
        """Decision 7: complete sets convert, incomplete sets land all-None."""

        from community_base.curriculum.models import Course

        self.run_import(apply=True)

        course = Course.objects.get(slug="p6-course")
        self.assertEqual(
            course.source_content_id,
            uuid.uuid5(PROVENANCE_NAMESPACE, "p6-course"),
        )
        self.assertEqual(course.source_commit_sha, _SHA)
        hidden = Course.objects.get(slug="p6-hidden")
        self.assertIsNone(hidden.source_content_id)
        self.assertIsNone(hidden.source_path)
        self.assertIsNone(hidden.source_commit_sha)
        self.assertIsNone(hidden.source_checksum)

    def test_the_cohort_renames_identifier_and_mode(self) -> None:
        """Decisions 5 and 9: identifier is the slug and live becomes cohort."""

        from community_base.curriculum.models import Cohort

        self.run_import(apply=True)

        cohort = Cohort.objects.get(course__slug="p6-course", slug="2026")
        self.assertEqual(cohort.mode, "cohort")
        self.assertEqual(cohort.hashtag, "#p62026")
        self.assertEqual(cohort.project_passing_score, 5)
        self.assertEqual(cohort.start_date.isoformat(), "2026-02-01")
        self_paced = Cohort.objects.get(course__slug="p6-course", slug="self-paced")
        self.assertEqual(self_paced.mode, "self_paced")

    def test_the_shared_graph_skips_and_counts_per_decision_11(self) -> None:
        from community_base.curriculum.models import Module, Unit

        report = self.run_import(apply=True)

        self.assertEqual(Module.objects.filter(course__slug="p6-course").count(), 1)
        module = Module.objects.get(course__slug="p6-course", slug="intro")
        self.assertEqual(module.sort_order, 1)
        self.assertEqual(module.overview, "Module overview text.")
        self.assertEqual(Unit.objects.filter(module=module).count(), 1)
        unit = Unit.objects.get(module=module, slug="welcome")
        self.assertEqual(unit.body, "Lesson body.")
        # Byte-identical: the site's rendered HTML, not a re-render.
        self.assertEqual(unit.body_html, "<p>Lesson body.</p>\n")
        self.assertEqual(module.overview_html, "<p>Module overview text.</p>\n")
        self.assertEqual(unit.video_url, "https://video.example.com/welcome")
        self.assertEqual(report["counts"]["shared_modules"]["skipped"], 1)
        self.assertEqual(report["counts"]["shared_lessons"]["skipped"], 3)
        self.assertEqual(report["gaps"]["shared_lessons:module_skipped_unpublished"], 1)
        self.assertEqual(report["gaps"]["lesson_code_sources"], 1)

    def test_placements_migrate_and_terminal_bindings_are_counted(self) -> None:
        """Decision 2: the binding has no package home; the placement still lands."""

        from community_base.curriculum.models import CohortModule

        report = self.run_import(apply=True)

        cohort_modules = CohortModule.objects.filter(cohort__slug="2026")
        self.assertEqual(cohort_modules.count(), 1)
        self.assertEqual(cohort_modules.get().sort_order, 1)
        self.assertEqual(report["gaps"]["terminal_homework_bindings"], 1)
        self.assertEqual(report["gaps"]["placements:module_skipped"], 1)

    def test_enrollments_keep_dates_and_certificates_keep_urls(self) -> None:
        from community_base.curriculum.models import Certificate, Enrollment

        self.run_import(apply=True)

        enrollment = Enrollment.objects.get(cohort__slug="2026", display_name="learner-display")
        self.assertEqual(enrollment.enrolled_at, _PAST)
        self.assertEqual(enrollment.certificate_name, "Learner Name")
        self.assertEqual(enrollment.total_score, 7)
        certificate = Certificate.objects.get(enrollment=enrollment)
        self.assertEqual(certificate.url, _CERT_URL)
        self.assertFalse(
            Enrollment.objects.filter(cohort__slug="2026", total_score=0)
            .exclude(certificate__isnull=True)
            .exists()
        )

    def test_progress_preserves_the_read_time(self) -> None:
        from community_base.curriculum.models import UnitProgress

        self.run_import(apply=True)

        unit_progress = UnitProgress.objects.filter(unit__module__course__slug="p6-course")
        self.assertEqual(unit_progress.count(), 1)
        self.assertFalse(unit_progress.filter(completed_at__isnull=True).exists())
        self.assertGreater(unit_progress.get().completed_at.year, 2020)

    def test_coursework_families_copy_and_derived_rows_stay_verbatim(self) -> None:
        """Decision 13: statistics and scores are copied, never recomputed."""

        from community_base.coursework import models as cw

        self.run_import(apply=True)

        homework = cw.Homework.objects.get(cohort__slug="2026", slug="hw1")
        self.assertEqual(homework.title, "Homework 1")
        self.assertIsNone(homework.source_content_id)
        self.assertEqual(homework.statistics.total_submissions, 1)
        self.assertEqual(homework.statistics.max_total_score, 7)
        question = cw.Question.objects.get(
            homework=homework,
            source_content_id=uuid.uuid5(PROVENANCE_NAMESPACE, "p6-course/homework-1/question-1"),
        )
        self.assertEqual(question.scores_for_correct_answer, 2)
        managed = cw.Question.objects.get(homework=homework, text="Free reflection")
        self.assertIsNone(managed.source_content_id)
        submission = cw.Submission.objects.get(homework=homework, total_score=7)
        self.assertEqual(submission.submitted_at, _PAST)
        self.assertEqual(submission.answers.count(), 2)

        project = cw.Project.objects.get(cohort__slug="2026", slug="final")
        self.assertEqual(project.criteria_assignments.count(), 1)
        self.assertEqual(project.submissions.count(), 2)
        self.assertEqual(project.statistics.total_submissions, 2)
        vote = cw.ProjectVote.objects.get(submission__project=project)
        self.assertEqual(vote.created_at, _PAST)
        review = cw.PeerReview.objects.get(reviewer__project=project)
        self.assertEqual(review.criteria_responses.get().answer, "2")
        self.assertEqual(
            cw.ProjectEvaluationScore.objects.filter(submission__project=project).count(),
            1,
        )

    def test_decision_18_answers_every_pooled_review_field(self) -> None:
        """Decision 18: review_state is derived; the pooled window and batch stay default."""

        from community_base.coursework import models as cw

        from courses import models as site

        if not _package_carries(cw.ProjectSubmission, "review_state"):
            self.skipTest("the pinned community-base release predates C5.2f")

        def review_states() -> set[str]:
            return set(
                cw.ProjectSubmission.objects.filter(project__slug="final").values_list(
                    "review_state", flat=True
                )
            )

        # The fixture project is still COLLECTING_SUBMISSIONS: nobody is assigned yet.
        self.run_import(apply=True)
        self.assertEqual(review_states(), {"AW"})

        site.Project.objects.filter(slug="final").update(
            state=site.ProjectState.PEER_REVIEWING.value
        )
        self.run_import(apply=True)
        self.assertEqual(review_states(), {"IR"})

        site.Project.objects.filter(slug="final").update(state=site.ProjectState.COMPLETED.value)
        self.run_import(apply=True)
        self.assertEqual(review_states(), {"SC"})

        # Pooled review itself does not migrate: no window is invented, no batch is formed.
        project = cw.Project.objects.get(cohort__slug="2026", slug="final")
        self.assertEqual(
            project.pooled_review_window_days,
            cw.Project._meta.get_field("pooled_review_window_days").default,
        )
        self.assertIsNone(cw.PeerReview.objects.get(reviewer__project=project).batch)
        self.assertEqual(cw.PeerReviewBatch.objects.count(), 0)

    def test_registration_and_editorial_families_copy(self) -> None:
        from community_base.coursework import models as cw

        self.run_import(apply=True)

        campaign = cw.RegistrationCampaign.objects.get(slug="p6-campaign")
        self.assertEqual(campaign.current_cohort.slug, "2026")
        learner = get_user_model().objects.get(username="p6-learner")
        peer = get_user_model().objects.get(username="p6-peer")
        registration = campaign.registrations.get(user=learner)
        self.assertEqual(registration.cohort.slug, "2026")
        self.assertEqual(registration.country, "Spain")
        # The site's null cohort survives the shared save() default.
        orphan = campaign.registrations.get(user=peer)
        self.assertIsNone(orphan.cohort)
        testimonial = cw.Testimonial.objects.get(course__slug="p6-course", name="Graduate")
        self.assertEqual(testimonial.quote, "The course changed how I work.")
        self.assertEqual(testimonial.placement, "course")
        wrapped = cw.WrappedStatistics.objects.get(year=2099)
        self.assertEqual(wrapped.user_statistics.get().total_points, 16)

    def test_the_gap_ledger_counts_every_decision(self) -> None:
        """Decisions 2-6 and 17 surface as counts, never as silent drops."""

        report = self.run_import(apply=True)

        self.assertEqual(report["gaps"]["flow_item_project_placements"], 1)
        self.assertNotIn("flow_item_module_placements", report["gaps"])
        self.assertEqual(report["counts"]["flow_items_leftovers"]["skipped"], 1)
        self.assertEqual(report["gaps"]["terminal_homework_bindings"], 1)
        self.assertEqual(report["gaps"]["lesson_code_sources"], 1)
        self.assertEqual(report["gaps"]["cohort_overrides"], 1)
        self.assertEqual(report["gaps"]["module_lesson_summaries"], 1)
        self.assertEqual(report["gaps"]["shared_curriculum_assets"], 1)


class SharedCoursePlatformImportRefusalsTests(ImportRunMixin, TestCase):
    """The import refuses rather than half-copies."""

    @classmethod
    def setUpTestData(cls) -> None:
        _import_family_graph()

    def test_a_course_without_a_shared_graph_refuses_the_run(self) -> None:
        """Decision 12: un-backfilled cohort-owned material stops everything."""

        from courses import models as site

        family = site.Course.objects.create(slug="p6-legacy", title="P6 Legacy")
        cohort = site.Cohort.objects.create(
            slug="p6-legacy-2020",
            course=family,
            identifier="2020",
            year=2020,
            title="P6 Legacy 2020",
            curriculum_format=site.CurriculumFormat.MODULES,
        )
        homework = site.Homework.objects.create(
            slug="legacy-hw",
            course=cohort,
            title="Legacy homework",
            due_date=datetime(2020, 3, 1, tzinfo=UTC),
        )
        module = site.Module.objects.create(
            cohort=cohort,
            position=1,
            slug="legacy-module",
            title="Legacy module",
            terminal_homework=homework,
        )
        site.Unit.objects.create(
            module=module,
            position=1,
            slug="legacy-unit",
            title="Legacy unit",
        )

        with self.assertRaises(SharedBackfillRequired) as caught:
            self.run_import(apply=True)

        self.assertIn("p6-legacy", caught.exception.course_slugs)

    def test_an_unknown_delivery_mode_refuses_the_run(self) -> None:
        """Refusal 2: a mode the mapping does not name stops the run, named.

        The site's own check constraint can only hold ``live`` or
        ``self_paced``, so the test disables SQLite check enforcement for one
        statement to manufacture the drifted row a production accident would
        need; the refusal must come from the importer, never the KeyError a
        blind dict lookup would raise.
        """

        from django.db import connection

        from courses import models as site

        cohort = site.Cohort.objects.get(slug="p6-course-self-paced")
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA ignore_check_constraints = ON")
            cursor.execute(
                "UPDATE courses_course SET delivery_mode = 'hybrid' WHERE id = %s",
                [cohort.pk],
            )
            cursor.execute("PRAGMA ignore_check_constraints = OFF")

        with self.assertRaises(UnknownDeliveryMode) as caught:
            self.run_import(apply=True)

        self.assertIn("p6-course/self-paced:hybrid", caught.exception.named)

    def test_a_mapping_gap_refuses_the_run(self) -> None:
        """Refusal 3: a field the registry does not name fails the run.

        Simulates model drift by hiding one field's verdict from the registry:
        the run must refuse naming the model, never drop the field quietly.
        """

        from unittest.mock import patch

        import scripts.prod.import_shared_course_platform as importer

        real_mapping = importer._mapping

        def hide_a_field():
            registry = real_mapping()
            site_fields, package_written, package_defaults = registry[
                ("courses.Cohort", "cb_curriculum.Cohort", False)
            ]
            reduced = {
                name: verdict
                for name, verdict in site_fields.items()
                if name != "first_homework_scored"
            }
            registry[("courses.Cohort", "cb_curriculum.Cohort", False)] = (
                reduced,
                package_written,
                package_defaults,
            )
            return registry

        with patch.object(importer, "_mapping", hide_a_field):
            with self.assertRaises(importer.MappingCoverageDrift) as caught:
                importer._refuse_mapping_drift()

        self.assertIn("courses.Cohort: first_homework_scored", caught.exception.models)

    def test_two_self_paced_cohorts_refuse_the_run(self) -> None:
        from courses import models as site

        family = site.Course.objects.get(slug="p6-course")
        site.Cohort.objects.create(
            slug="p6-course-self-paced-2",
            course=family,
            identifier="self-paced-2",
            year=2023,
            title="P6 Course Self-paced 2",
            delivery_mode=site.DeliveryMode.SELF_PACED,
        )

        with self.assertRaises(SelfPacedCohortNotUnique) as caught:
            self.run_import(apply=True)

        self.assertEqual(caught.exception.course_slugs, ("p6-course",))

    def test_ambiguous_duplicate_rows_refuse_the_count_verification(self) -> None:
        from courses import models as site

        cohort = site.Cohort.objects.get(slug="p6-course-2026")
        values = {
            "course": cohort,
            "description": "Identical",
            "options": [{"criteria": "Poor", "score": 0}],
            "review_criteria_type": site.ReviewCriteriaTypes.RADIO_BUTTONS.value,
        }
        site.ReviewCriteria.objects.create(**values)
        site.ReviewCriteria.objects.create(**values)

        with self.assertRaises(CountMismatch) as caught:
            self.run_import(apply=True)

        self.assertIn("review_criteria", caught.exception.families)
