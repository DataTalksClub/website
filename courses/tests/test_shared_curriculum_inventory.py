"""Shared-curriculum rollout inventory service and command tests (W7)."""

import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from courses.models import (
    Cohort,
    CohortSharedModule,
    Course,
    CurriculumRouteAlias,
    CurriculumFormat,
    CurriculumSource,
    DeliveryMode,
    Enrollment,
    Homework,
    Project,
    ProjectSubmission,
    SharedCurriculum,
    SharedLesson,
    SharedModule,
    Submission,
)
from courses.services.shared_curriculum_inventory import (
    SharedCurriculumInventoryError,
    build_inventory,
    load_decisions,
    validate_decisions,
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


class InventoryWorldTestCase(TestCase):
    """One family with a live shared cohort, a self-paced one, an archive."""

    def setUp(self) -> None:
        super().setUp()
        self.course = Course.objects.create(slug="inv-zoomcamp", title="Inv Zoomcamp")
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
        SharedLesson.objects.create(
            module=self.module,
            position=0,
            slug="01-lesson",
            title="Introduction",
            **provenance("33333333-3333-4333-8333-333333333333", "01-agentic-rag/01-lesson.md"),
        )

        self.cohort_live = Cohort.objects.create(
            course=self.course,
            slug="inv-zoomcamp-2026",
            identifier="2026",
            year=2026,
            title="Inv Zoomcamp 2026",
            description="live delivery",
            curriculum_format=CurriculumFormat.SHARED,
            delivery_mode=DeliveryMode.LIVE,
        )
        self.cohort_live.shared_curriculum = self.shared
        self.cohort_live.save()
        self.homework = Homework.objects.create(
            course=self.cohort_live,
            slug="hw1",
            title="Homework 1",
            due_date=timezone.now() + timedelta(days=7),
            **provenance("51111111-1111-4111-8111-111111111111", "cohorts/2026/homework/hw1"),
        )
        CohortSharedModule.objects.create(
            cohort=self.cohort_live,
            shared_module=self.module,
            position=0,
            terminal_homework=self.homework,
        )
        self.project = Project.objects.create(
            course=self.cohort_live,
            slug="project-01",
            title="Project 1",
            submission_due_date=timezone.now() + timedelta(days=7),
            peer_review_due_date=timezone.now() + timedelta(days=8),
        )

        self.cohort_self_paced = Cohort.objects.create(
            course=self.course,
            slug="inv-zoomcamp-self-paced",
            identifier="self-paced",
            year=2027,
            title="Inv Zoomcamp Self-Paced",
            description="ungraded practice",
            curriculum_format=CurriculumFormat.SHARED,
            delivery_mode=DeliveryMode.SELF_PACED,
        )
        self.cohort_self_paced.shared_curriculum = self.shared
        self.cohort_self_paced.save()

        self.cohort_archive = Cohort.objects.create(
            course=self.course,
            slug="inv-zoomcamp-2025",
            identifier="2025",
            year=2025,
            title="Inv Zoomcamp 2025",
            description="github archive",
            curriculum_format=CurriculumFormat.LEGACY,
            delivery_mode=DeliveryMode.LIVE,
            curriculum_source=CurriculumSource.GITHUB_ARCHIVE,
            archive_notice_path="cohorts/2025/README.md",
            archive_url=(
                "https://github.com/DataTalksClub/inv-zoomcamp/blob/"
                + SHA
                + "/cohorts/2025/README.md"
            ),
            archive_commit_sha=SHA,
        )
        self.archive_homework = Homework.objects.create(
            course=self.cohort_archive,
            slug="hw-old",
            title="Homework 1 (2025)",
            due_date=timezone.now() - timedelta(days=400),
        )
        self.alias = CurriculumRouteAlias.objects.create(
            old_path="/courses/inv-zoomcamp/2025/01-old-module",
            target_kind=CurriculumRouteAlias.TargetKind.GITHUB_ARCHIVE,
            archive_cohort=self.cohort_archive,
            archive_path="cohorts/2025/01-old-module",
            reason="Reviewed archive alias from the legacy lesson shape.",
            source_commit_sha=SHA,
        )

        self.learner = get_user_model().objects.create_user(username="inv-learner-7")
        self.enrollment = Enrollment.objects.create(
            student=self.learner, course=self.cohort_live
        )
        Submission.objects.create(
            homework=self.homework,
            student=self.learner,
            enrollment=self.enrollment,
        )
        ProjectSubmission.objects.create(
            project=self.project,
            student=self.learner,
            enrollment=self.enrollment,
            github_link="https://github.com/example/project-01",
            commit_id="c" * 40,
        )

    def by_identifier(self, inventory: dict) -> dict:
        return {cohort["identifier"]: cohort for cohort in inventory["cohorts"]}


class InventoryServiceTests(InventoryWorldTestCase):
    def test_inventory_is_complete_bounded_and_content_free(self) -> None:
        inventory = build_inventory(course_slug="inv-zoomcamp")
        rendered = json.dumps(inventory, sort_keys=True)

        self.assertTrue(inventory["course"]["shared_curriculum"]["present"])
        self.assertEqual(inventory["course"]["shared_curriculum"]["modules_published"], 1)
        self.assertEqual(inventory["course"]["shared_curriculum"]["lessons_published"], 1)

        cohorts = self.by_identifier(inventory)
        self.assertEqual(set(cohorts), {"2026", "self-paced", "2025"})

        live = cohorts["2026"]
        self.assertEqual(live["delivery_mode"], "live")
        self.assertEqual(live["curriculum_source"], "current")
        self.assertEqual(live["source_content_id"], None)
        self.assertEqual(len(live["shared_placements"]), 1)
        placement = live["shared_placements"][0]
        self.assertEqual(placement["module_slug"], "01-agentic-rag")
        self.assertEqual(placement["terminal_homework_slug"], "hw1")
        self.assertEqual(
            [row["slug"] for row in live["homework"]],
            ["hw1"],
        )
        self.assertEqual(live["counts"], {
            "enrollments": 1,
            "homework_submissions": 1,
            "project_submissions": 1,
        })

        # Content-free: no learner identifier, username, or submission body
        # ever enters the report; people appear only as counts.
        self.assertNotIn("inv-learner-7", rendered)
        self.assertNotIn("github.com/example/project-01", rendered)

    def test_self_paced_and_archive_records_surface_their_contract(self) -> None:
        inventory = build_inventory(course_slug="inv-zoomcamp")
        cohorts = self.by_identifier(inventory)

        self.assertEqual(cohorts["self-paced"]["delivery_mode"], "self_paced")
        self.assertEqual(cohorts["self-paced"]["shared_placements"], [])
        self.assertEqual(cohorts["self-paced"]["homework"], [])

        archive = cohorts["2025"]
        self.assertEqual(archive["curriculum_source"], "github_archive")
        self.assertEqual(archive["archive_identity"], {
            "notice_path": "cohorts/2025/README.md",
            "commit_sha": SHA,
        })
        self.assertEqual(
            archive["route_aliases"],
            ["/courses/inv-zoomcamp/2025/01-old-module"],
        )
        self.assertEqual(archive["counts"]["homework_submissions"], 0)

    def test_unknown_course_fails_closed(self) -> None:
        with self.assertRaises(SharedCurriculumInventoryError) as raised:
            build_inventory(course_slug="no-such-family")
        self.assertEqual(raised.exception.conflicts[0]["kind"], "course")


class DecisionValidationTests(InventoryWorldTestCase):
    def decisions(self) -> dict:
        return build_inventory(course_slug="inv-zoomcamp")

    def test_missing_decisions_fail_closed(self) -> None:
        with self.assertRaises(SharedCurriculumInventoryError) as raised:
            validate_decisions(self.decisions(), {})
        kinds = {conflict["kind"] for conflict in raised.exception.conflicts}
        self.assertEqual(kinds, {"decision_missing"})

    def test_unknown_cohort_decision_is_rejected(self) -> None:
        with self.assertRaises(SharedCurriculumInventoryError) as raised:
            validate_decisions(
                self.decisions(),
                {"2026": "keep-current", "self-paced": "keep-current", "2025": "keep-current", "2099": "keep-current"},
            )
        self.assertEqual(
            raised.exception.conflicts[0]["kind"], "decision_unknown_cohort"
        )

    def test_self_paced_cohort_is_never_archived(self) -> None:
        with self.assertRaises(SharedCurriculumInventoryError) as raised:
            validate_decisions(
                self.decisions(),
                {
                    "2026": "keep-current",
                    "self-paced": "become-archive",
                    "2025": "keep-current",
                },
            )
        self.assertEqual(
            raised.exception.conflicts[0]["kind"], "decision_self_paced_archive"
        )

    def test_archive_with_submitted_work_requires_acknowledgement(self) -> None:
        decisions = {
            "2026": "become-archive",
            "self-paced": "keep-current",
            "2025": "keep-current",
        }
        with self.assertRaises(SharedCurriculumInventoryError) as raised:
            validate_decisions(self.decisions(), decisions)
        self.assertEqual(
            raised.exception.conflicts[0]["kind"], "decision_submitted_work"
        )

        decisions["2026"] = {
            "decision": "become-archive",
            "acknowledge_submitted_work": True,
        }
        resolved = validate_decisions(self.decisions(), decisions)
        self.assertEqual(resolved["2026"], "become-archive")
        self.assertEqual(resolved["self-paced"], "keep-current")

    def test_decisions_file_shape_is_checked(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text("[]", encoding="utf-8")
            with self.assertRaises(SharedCurriculumInventoryError):
                load_decisions(str(bad))

            good = Path(tmp) / "good.json"
            good.write_text(
                json.dumps(
                    {
                        "decisions": {
                            "2026": "keep-current",
                            "self-paced": "keep-current",
                            "2025": "become-archive",
                        }
                    }
                ),
                encoding="utf-8",
            )
            resolved = validate_decisions(
                self.decisions(), load_decisions(str(good))
            )
            self.assertEqual(resolved["2025"], "become-archive")


class InventoryCommandTests(InventoryWorldTestCase):
    def test_command_emits_bounded_pending_report(self) -> None:
        from io import StringIO

        out = StringIO()
        call_command(
            "shared_curriculum_inventory", "--course", "inv-zoomcamp", stdout=out
        )
        report = json.loads(out.getvalue())

        self.assertEqual(report["decision_coverage"], "pending")
        self.assertNotIn("decisions", report)
        self.assertEqual(
            {row["identifier"] for row in report["cohorts"]},
            {"2026", "self-paced", "2025"},
        )

    def test_command_validates_and_merges_reviewed_decisions(self) -> None:
        from io import StringIO
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "decisions.json"
            path.write_text(
                json.dumps(
                    {
                        "decisions": {
                            "2026": "keep-current",
                            "self-paced": "keep-current",
                            "2025": "become-archive",
                        }
                    }
                ),
                encoding="utf-8",
            )
            out = StringIO()
            call_command(
                "shared_curriculum_inventory",
                "--course",
                "inv-zoomcamp",
                "--decisions",
                str(path),
                stdout=out,
            )
            report = json.loads(out.getvalue())

        self.assertEqual(report["decision_coverage"], "reviewed")
        self.assertEqual(report["decisions"]["2025"], "become-archive")

    def test_command_reports_conflicts_as_bounded_json(self) -> None:
        with self.assertRaises(CommandError) as raised:
            call_command(
                "shared_curriculum_inventory", "--course", "no-such-family"
            )
        self.assertIn('"kind": "course"', str(raised.exception))
