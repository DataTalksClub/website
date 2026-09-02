from __future__ import annotations

import hashlib
import importlib
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader

from test_support.factories.context import canonical_json_bytes
from test_support.migrations import (
    assert_data_migration_isolation,
    assert_stable_migration_module_isolation,
    load_migration_seed,
)

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "test_support" / "migration_seeds" / "courses-legacy-history-v1.json"
LEGACY_TARGET = ("courses", "0041_courseregistrationcountsourcerun_and_more")
REPAIRED_TARGET = ("courses", "0052_merge_duplicate_course_families")
# The family repair is the seed's replay target; the leaf moves on with every
# ordinary product migration that lands after it.
LEAF_TARGET = ("courses", "0053_unit_lesson_video_and_code_sources")
FROZEN_AT = datetime(2025, 9, 1, 12, 0, tzinfo=UTC)


class CourseMigrationHistoryCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        digest = hashlib.sha256(self.id().encode("utf-8")).hexdigest()[:12]
        self.worker_id = f"course-migration-{digest}"
        layout = settings.TEST_RUNTIME.worker(self.worker_id)
        database_path = settings.TEST_RUNTIME.assert_database_path(
            layout.database,
            worker_id=self.worker_id,
        )
        self.connection = connections["default"]
        self.connection.close()
        self.original_name = self.connection.settings_dict["NAME"]
        self.original_test = self.connection.settings_dict["TEST"]
        self.original_worker = self.connection.settings_dict["DTC_WORKER_ID"]
        self.connection.settings_dict["NAME"] = database_path
        self.connection.settings_dict["TEST"] = {"NAME": database_path}
        self.connection.settings_dict["DTC_WORKER_ID"] = self.worker_id

    def tearDown(self) -> None:
        self.connection.close()
        self.connection.settings_dict["NAME"] = self.original_name
        self.connection.settings_dict["TEST"] = self.original_test
        self.connection.settings_dict["DTC_WORKER_ID"] = self.original_worker
        self.connection.connect()
        super().tearDown()

    def test_loader_preserves_legacy_identities_and_fresh_reaches_repaired_leaf(self) -> None:
        raw_loader = MigrationLoader(self.connection, replace_migrations=False)
        for number in range(1, 42):
            matching = [
                key
                for key in raw_loader.disk_migrations
                if key[0] == "courses" and key[1][:4].isdigit() and int(key[1][:4]) == number
            ]
            self.assertGreaterEqual(len(matching), 1, number)
        self.assertIn(("courses", "0001_initial"), raw_loader.disk_migrations)
        self.assertIn(LEGACY_TARGET, raw_loader.graph.nodes)

        repaired_loader = MigrationLoader(self.connection)
        self.assertIn(("courses", "0001_squashed_0029"), repaired_loader.graph.nodes)
        self.assertNotIn(("courses", "0001_initial"), repaired_loader.graph.nodes)
        self.assertIn(REPAIRED_TARGET, repaired_loader.graph.nodes)
        self.assertIn(LEAF_TARGET, repaired_loader.graph.leaf_nodes())

        executor = MigrationExecutor(self.connection)
        executor.migrate([REPAIRED_TARGET])
        apps = MigrationExecutor(self.connection).loader.project_state([REPAIRED_TARGET]).apps
        self.assertEqual(
            apps.get_model("courses", "Course")._meta.db_table, "courses_course_family"
        )
        self.assertEqual(apps.get_model("courses", "Cohort")._meta.db_table, "courses_course")
        self.assertTrue(apps.get_model("courses", "Cohort")._meta.get_field("uuid").unique)

    def test_squash_keeps_the_0027_branch_for_the_0031_merge(self) -> None:
        squash_module = importlib.import_module("courses.migrations.0001_squashed_0029")
        replaced = set(squash_module.Migration.replaces)
        branch = ("courses", "0027_homework_instructions_url_project_instructions_url_and_more")
        merge = ("courses", "0031_merge_instruction_urls_and_profile_fields")
        account_branch = ("courses", "0030_remove_enrollment_profile_fields")

        self.assertNotIn(branch, replaced)

        loader = MigrationLoader(self.connection)
        self.assertIn(branch, loader.graph.nodes)
        self.assertEqual(
            {node.key for node in loader.graph.node_map[merge].parents},
            {branch, account_branch},
        )
        fresh_plan = loader.graph.forwards_plan(REPAIRED_TARGET)
        self.assertLess(fresh_plan.index(account_branch), fresh_plan.index(branch))
        self.assertLess(fresh_plan.index(branch), fresh_plan.index(merge))

    def test_populated_legacy_history_preserves_rows_fks_checksums_and_provenance(self) -> None:
        seed = load_migration_seed(SEED)
        self.assertEqual(seed.start, LEGACY_TARGET)
        self.assertEqual(seed.target, REPAIRED_TARGET)

        _, legacy_apps = self._migrate(LEGACY_TARGET, replace_migrations=False)
        self._populate_legacy_fixture(legacy_apps, seed)
        before = self._data_snapshot(legacy_apps, course_model="Course")
        before_provenance = self._migration_snapshot()

        _, repaired_apps = self._migrate(REPAIRED_TARGET)
        after = self._data_snapshot(repaired_apps, course_model="Cohort")
        after_provenance = self._migration_snapshot()

        self.assertEqual(after["rows"], before["rows"])
        self.assertEqual(after["foreign_keys"], before["foreign_keys"])
        self.assertEqual(after["checksum"], before["checksum"])
        self.assertEqual(
            after["content_types_and_permissions"], before["content_types_and_permissions"]
        )
        self.assertTrue(before_provenance <= after_provenance)
        self.assertIn(("courses", "0042_course_schema_bridge"), after_provenance)
        self.assertIn(REPAIRED_TARGET, after_provenance)

        family_id = UUID("e83e29ae-c836-57d7-bb13-d3abddcd4338")
        Course = repaired_apps.get_model("courses", "Course")
        Cohort = repaired_apps.get_model("courses", "Cohort")
        family = Course.objects.get(pk=family_id)
        cohort = Cohort.objects.get(pk=seed.payload["cohort"]["id"])
        self.assertEqual(family.slug, seed.expected["family_slug"])
        self.assertEqual(cohort.course_id, family.pk)
        self.assertEqual(str(cohort.uuid), "1b8f6172-caa4-5912-ac67-3cadd6ad909d")
        self.assertEqual(cohort.year, seed.expected["cohort_year"])
        self.assertEqual(Cohort.objects.count(), 1)
        self.assertEqual(Course.objects.count(), 1)

        executor = MigrationExecutor(self.connection)
        self.assertEqual(executor.migration_plan([REPAIRED_TARGET]), [])
        replay_before = self._data_snapshot(repaired_apps, course_model="Cohort")
        executor.migrate([REPAIRED_TARGET])
        replay_after = self._data_snapshot(
            MigrationExecutor(self.connection).loader.project_state([REPAIRED_TARGET]).apps,
            course_model="Cohort",
        )
        self.assertEqual(replay_after, replay_before)

    def test_bridge_failure_rolls_back_and_retry_is_idempotent(self) -> None:
        seed = load_migration_seed(SEED)
        _, legacy_apps = self._migrate(LEGACY_TARGET, replace_migrations=False)
        self._populate_legacy_fixture(legacy_apps, seed)

        bridge_module = importlib.import_module("courses.migrations.0042_course_schema_bridge")
        bridge_operation = next(
            operation
            for operation in bridge_module.Migration.operations
            if operation.__class__.__name__ == "RunPython"
        )
        original_code = bridge_operation.code

        def fail_after_backfill(apps, schema_editor):
            original_code(apps, schema_editor)
            raise RuntimeError("synthetic bridge failure")

        bridge_operation.code = fail_after_backfill
        try:
            with self.assertRaisesRegex(RuntimeError, "synthetic bridge failure"):
                self._migrate(REPAIRED_TARGET)
        finally:
            bridge_operation.code = original_code

        self.assertNotIn("courses_course_family", self.connection.introspection.table_names())
        _, repaired_apps = self._migrate(REPAIRED_TARGET)
        Course = repaired_apps.get_model("courses", "Course")
        Cohort = repaired_apps.get_model("courses", "Cohort")
        self.assertEqual(Course.objects.count(), 1)
        self.assertEqual(Cohort.objects.count(), 1)
        self.assertEqual(
            Cohort.objects.get(pk=seed.payload["cohort"]["id"]).course_id,
            UUID("e83e29ae-c836-57d7-bb13-d3abddcd4338"),
        )

    def test_course_data_migrations_are_historical_and_import_safe(self) -> None:
        for name in (
            "0001_squashed_0029.py",
            "0042_course_schema_bridge.py",
            "0043_curriculum_and_project_criteria.py",
            "0046_cohort_identifier_and_more.py",
            "0052_merge_duplicate_course_families.py",
        ):
            with self.subTest(name=name):
                assert_data_migration_isolation(ROOT / "courses" / "migrations" / name)
        assert_stable_migration_module_isolation(ROOT / "courses" / "migration_family_identity.py")
        MigrationLoader(self.connection, replace_migrations=False)

    def _migrate(self, target: tuple[str, str], *, replace_migrations: bool = True):
        executor = MigrationExecutor(self.connection)
        executor.loader = MigrationLoader(
            self.connection,
            replace_migrations=replace_migrations,
        )
        executor.migrate([target])
        apps = executor.loader.project_state([target]).apps
        return executor, apps

    def _populate_legacy_fixture(self, apps, seed) -> None:
        payload = seed.payload
        User = apps.get_model("accounts", "CustomUser")
        Course = apps.get_model("courses", "Course")
        Enrollment = apps.get_model("courses", "Enrollment")
        Homework = apps.get_model("courses", "Homework")
        Question = apps.get_model("courses", "Question")
        Submission = apps.get_model("courses", "Submission")
        Answer = apps.get_model("courses", "Answer")
        Project = apps.get_model("courses", "Project")
        ProjectSubmission = apps.get_model("courses", "ProjectSubmission")
        ReviewCriteria = apps.get_model("courses", "ReviewCriteria")
        PeerReview = apps.get_model("courses", "PeerReview")
        CriteriaResponse = apps.get_model("courses", "CriteriaResponse")
        ProjectEvaluationScore = apps.get_model("courses", "ProjectEvaluationScore")
        HomeworkStatistics = apps.get_model("courses", "HomeworkStatistics")
        ProjectStatistics = apps.get_model("courses", "ProjectStatistics")
        WrappedStatistics = apps.get_model("courses", "WrappedStatistics")
        UserWrappedStatistics = apps.get_model("courses", "UserWrappedStatistics")
        RegistrationCampaign = apps.get_model("courses", "RegistrationCampaign")
        CourseRegistration = apps.get_model("courses", "CourseRegistration")
        CountSourceRun = apps.get_model("courses", "CourseRegistrationCountSourceRun")
        CountRevision = apps.get_model("courses", "CourseRegistrationCountRevision")
        CountSlot = apps.get_model("courses", "CourseRegistrationCountSlot")

        user = payload["user"]
        User.objects.create(
            id=user["id"],
            username=user["username"],
            email=user["email"],
            password="synthetic-password-hash",
        )
        cohort = payload["cohort"]
        Course.objects.create(
            id=cohort["id"],
            slug=cohort["slug"],
            title=cohort["title"],
            description=cohort["description"],
            social_media_hashtag=cohort["social_media_hashtag"],
            faq_document_url=cohort["faq_document_url"],
            first_homework_scored=cohort["first_homework_scored"],
            finished=cohort["finished"],
            homework_problems_comments_field=cohort["homework_problems_comments_field"],
            project_passing_score=cohort["project_passing_score"],
            min_projects_to_pass=cohort["min_projects_to_pass"],
            visible=cohort["visible"],
            end_date=cohort["end_date"],
            registration_url=cohort["registration_url"],
            github_repo_url=cohort["github_repo_url"],
            start_date=cohort["start_date"],
        )
        enrollment = payload["enrollment"]
        Enrollment.objects.create(
            id=enrollment["id"],
            course_id=enrollment["course_id"],
            student_id=enrollment["student_id"],
            display_name=enrollment["display_name"],
            display_on_leaderboard=enrollment["display_on_leaderboard"],
            certificate_name=enrollment["certificate_name"],
            certificate_url=enrollment["certificate_url"],
            total_score=enrollment["total_score"],
        )
        homework = payload["homework"]
        Homework.objects.create(
            id=homework["id"],
            course_id=homework["course_id"],
            slug=homework["slug"],
            title=homework["title"],
            description=homework["description"],
            due_date=homework["due_date"],
            homework_url_field=homework["homework_url_field"],
            time_spent_lectures_field=homework["time_spent_lectures_field"],
            time_spent_homework_field=homework["time_spent_homework_field"],
            faq_contribution_field=homework["faq_contribution_field"],
            state=homework["state"],
        )
        question = payload["question"]
        question_values = dict(question)
        question_values.pop("id")
        Question.objects.create(id=question["id"], **question_values)
        submission = payload["submission"]
        Submission.objects.create(
            id=submission["id"],
            enrollment_id=submission["enrollment_id"],
            homework_id=submission["homework_id"],
            student_id=submission["student_id"],
            homework_link=submission["homework_link"],
            faq_contribution=submission["faq_contribution"],
            problems_comments=submission["problems_comments"],
            total_score=submission["total_score"],
        )
        answer = payload["answer"]
        Answer.objects.create(
            id=answer["id"],
            answer_text=answer["answer_text"],
            is_correct=answer["is_correct"],
            question_id=answer["question_id"],
            submission_id=answer["submission_id"],
        )
        project = payload["project"]
        Project.objects.create(
            id=project["id"],
            course_id=project["course_id"],
            slug=project["slug"],
            title=project["title"],
            submission_due_date=project["submission_due_date"],
            peer_review_due_date=project["peer_review_due_date"],
            instructions_url=project["instructions_url"],
            state=project["state"],
        )
        project_submission = payload["project_submission"]
        ProjectSubmission.objects.create(
            id=project_submission["id"],
            project_id=project_submission["project_id"],
            student_id=project_submission["student_id"],
            enrollment_id=project_submission["enrollment_id"],
            github_link=project_submission["github_link"],
            commit_id=project_submission["commit_id"],
            passed=project_submission["passed"],
            total_score=project_submission["total_score"],
        )
        criteria = payload["review_criteria"]
        ReviewCriteria.objects.create(
            id=criteria["id"],
            course_id=criteria["course_id"],
            description=criteria["description"],
            options=criteria["options"],
            review_criteria_type=criteria["review_criteria_type"],
        )
        review = payload["peer_review"]
        PeerReview.objects.create(
            id=review["id"],
            note_to_peer=review["note_to_peer"],
            reviewer_id=review["reviewer_submission_id"],
            submission_under_evaluation_id=review["submission_under_evaluation_id"],
            state=review["state"],
        )
        response = payload["criteria_response"]
        CriteriaResponse.objects.create(
            id=response["id"],
            answer=response["answer"],
            criteria_id=response["criteria_id"],
            review_id=response["review_id"],
        )
        score = payload["project_evaluation_score"]
        ProjectEvaluationScore.objects.create(
            id=score["id"],
            score=score["score"],
            review_criteria_id=score["review_criteria_id"],
            submission_id=score["submission_id"],
        )
        homework_stats = payload["homework_statistics"]
        HomeworkStatistics.objects.create(
            id=homework_stats["id"],
            homework_id=homework_stats["homework_id"],
            last_calculated=homework_stats["last_calculated"],
            total_submissions=homework_stats["total_submissions"],
        )
        project_stats = payload["project_statistics"]
        ProjectStatistics.objects.create(
            id=project_stats["id"],
            project_id=project_stats["project_id"],
            last_calculated=project_stats["last_calculated"],
            total_submissions=project_stats["total_submissions"],
        )
        wrapped = payload["wrapped_statistics"]
        WrappedStatistics.objects.create(
            id=wrapped["id"],
            year=wrapped["year"],
            is_visible=wrapped["is_visible"],
            total_enrollments=wrapped["total_enrollments"],
            total_participants=wrapped["total_participants"],
        )
        user_wrapped = payload["user_wrapped_statistics"]
        UserWrappedStatistics.objects.create(
            id=user_wrapped["id"],
            user_id=user_wrapped["user_id"],
            wrapped_id=user_wrapped["wrapped_id"],
            display_name=user_wrapped["display_name"],
            rank=user_wrapped["rank"],
        )
        campaign = payload["registration_campaign"]
        RegistrationCampaign.objects.create(
            id=campaign["id"],
            current_course_id=campaign["current_course_id"],
            slug=campaign["slug"],
            title=campaign["title"],
        )
        registration = payload["course_registration"]
        CourseRegistration.objects.create(
            id=registration["id"],
            campaign_id=registration["campaign_id"],
            course_id=registration["course_id"],
            user_id=user["id"],
            email=registration["email"],
            email_normalized=registration["email_normalized"],
            name=registration["name"],
            country=registration["country"],
            region=registration["region"],
            role=registration["role"],
            accepted_newsletter=registration["accepted_newsletter"],
            company_name=registration["company_name"],
        )
        source = payload["count_source_run"]
        CountSourceRun.objects.create(
            id=source["id"],
            actor_id=user["id"],
            adapter_version=source["adapter_version"],
            schema_version=source["schema_version"],
            count_policy_version=source["count_policy_version"],
            whole_source_checksum=source["whole_source_checksum"],
            source_byte_size=128,
            schema_contract_checksum=source["schema_contract_checksum"],
            aggregate_manifest_checksum=source["aggregate_manifest_checksum"],
            source_reference_digest=source["source_reference_digest"],
            captured_at=source["captured_at"],
            source_frozen_at=source["source_frozen_at"],
            campaign_total=source["campaign_total"],
            row_total=source["row_total"],
            state=source["state"],
        )
        revision = payload["count_revision"]
        CountRevision.objects.create(
            id=revision["id"],
            source_run_id=revision["source_run_id"],
            campaign_id=revision["campaign_id"],
            cohort_id=revision["cohort_id"],
            campaign_slug_snapshot=campaign["slug"],
            cohort_slug_snapshot=cohort["slug"],
            baseline_count=revision["baseline_count"],
            source_min_created_at=revision["source_min_created_at"],
            source_max_created_at=revision["source_max_created_at"],
            coverage_cutoff_at=revision["coverage_cutoff_at"],
            proposed_native_start_at=revision["proposed_native_start_at"],
            aggregate_checksum=revision["aggregate_checksum"],
            state=revision["state"],
        )
        slot = payload["count_slot"]
        CountSlot.objects.create(
            id=slot["id"],
            campaign_id=slot["campaign_id"],
            cohort_id=slot["cohort_id"],
            active_baseline_revision_id=slot["active_baseline_revision_id"],
            mode=slot["mode"],
            native_start_at=slot["native_start_at"],
            prior_mode=slot["prior_mode"],
        )

    def _data_snapshot(self, apps, *, course_model: str) -> dict[str, Any]:
        models = {
            "course": apps.get_model("courses", course_model),
            "enrollment": apps.get_model("courses", "Enrollment"),
            "homework": apps.get_model("courses", "Homework"),
            "question": apps.get_model("courses", "Question"),
            "submission": apps.get_model("courses", "Submission"),
            "answer": apps.get_model("courses", "Answer"),
            "project": apps.get_model("courses", "Project"),
            "project_submission": apps.get_model("courses", "ProjectSubmission"),
            "review_criteria": apps.get_model("courses", "ReviewCriteria"),
            "peer_review": apps.get_model("courses", "PeerReview"),
            "criteria_response": apps.get_model("courses", "CriteriaResponse"),
            "project_evaluation_score": apps.get_model("courses", "ProjectEvaluationScore"),
            "homework_statistics": apps.get_model("courses", "HomeworkStatistics"),
            "project_statistics": apps.get_model("courses", "ProjectStatistics"),
            "wrapped_statistics": apps.get_model("courses", "WrappedStatistics"),
            "user_wrapped_statistics": apps.get_model("courses", "UserWrappedStatistics"),
            "registration_campaign": apps.get_model("courses", "RegistrationCampaign"),
            "course_registration": apps.get_model("courses", "CourseRegistration"),
            "count_source_run": apps.get_model("courses", "CourseRegistrationCountSourceRun"),
            "count_revision": apps.get_model("courses", "CourseRegistrationCountRevision"),
            "count_slot": apps.get_model("courses", "CourseRegistrationCountSlot"),
        }
        fields = {
            "course": [
                "id",
                "slug",
                "title",
                "description",
                "social_media_hashtag",
                "faq_document_url",
                "first_homework_scored",
                "finished",
                "homework_problems_comments_field",
                "project_passing_score",
                "min_projects_to_pass",
                "visible",
                "end_date",
                "registration_url",
                "github_repo_url",
                "start_date",
            ],
            "enrollment": [
                "id",
                "student_id",
                "course_id",
                "display_name",
                "display_on_leaderboard",
                "certificate_name",
                "certificate_url",
                "total_score",
            ],
            "homework": ["id", "course_id", "slug", "title", "description", "due_date", "state"],
            "question": ["id", "homework_id", "text", "possible_answers", "correct_answer"],
            "submission": [
                "id",
                "enrollment_id",
                "homework_id",
                "student_id",
                "homework_link",
                "total_score",
            ],
            "answer": ["id", "question_id", "submission_id", "answer_text", "is_correct"],
            "project": ["id", "course_id", "slug", "title", "state", "instructions_url"],
            "project_submission": [
                "id",
                "project_id",
                "student_id",
                "enrollment_id",
                "github_link",
                "commit_id",
                "passed",
                "total_score",
            ],
            "review_criteria": [
                "id",
                "course_id",
                "description",
                "options",
                "review_criteria_type",
            ],
            "peer_review": [
                "id",
                "reviewer_id",
                "submission_under_evaluation_id",
                "note_to_peer",
                "state",
            ],
            "criteria_response": ["id", "criteria_id", "review_id", "answer"],
            "project_evaluation_score": ["id", "review_criteria_id", "submission_id", "score"],
            "homework_statistics": ["id", "homework_id", "total_submissions", "last_calculated"],
            "project_statistics": ["id", "project_id", "total_submissions", "last_calculated"],
            "wrapped_statistics": [
                "id",
                "year",
                "is_visible",
                "total_participants",
                "total_enrollments",
            ],
            "user_wrapped_statistics": ["id", "user_id", "wrapped_id", "display_name", "rank"],
            "registration_campaign": ["id", "current_course_id", "slug", "title", "is_active"],
            "course_registration": [
                "id",
                "campaign_id",
                "course_id",
                "user_id",
                "email",
                "email_normalized",
                "name",
                "country",
                "region",
                "role",
                "accepted_newsletter",
                "company_name",
            ],
            "count_source_run": [
                "id",
                "actor_id",
                "source_reference_digest",
                "whole_source_checksum",
                "state",
                "campaign_total",
                "row_total",
            ],
            "count_revision": [
                "id",
                "source_run_id",
                "campaign_id",
                "cohort_id",
                "baseline_count",
                "aggregate_checksum",
                "state",
            ],
            "count_slot": [
                "id",
                "campaign_id",
                "cohort_id",
                "active_baseline_revision_id",
                "mode",
                "native_start_at",
                "prior_mode",
            ],
        }
        rows = {
            name: [
                {field: self._json_value(value) for field, value in row.items()}
                for row in model.objects.order_by("pk").values(*fields[name])
            ]
            for name, model in models.items()
        }
        foreign_keys = {
            name: {
                field: [row[field] for row in values]
                for field in fields[name]
                if field.endswith("_id")
            }
            for name, values in rows.items()
        }
        return {
            "rows": {name: len(values) for name, values in rows.items()},
            "foreign_keys": foreign_keys,
            "checksum": hashlib.sha256(canonical_json_bytes(rows)).hexdigest(),
            "content_types_and_permissions": self._content_type_snapshot(),
        }

    def _content_type_snapshot(self) -> list[tuple[Any, ...]]:
        with self.connection.cursor() as cursor:
            tables = self.connection.introspection.table_names(cursor)
            values: list[tuple[Any, ...]] = []
            if "django_content_type" in tables:
                cursor.execute(
                    "SELECT app_label, model FROM django_content_type ORDER BY app_label, model"
                )
                values.extend(("content_type", *row) for row in cursor.fetchall())
            if "auth_permission" in tables:
                cursor.execute(
                    "SELECT content_type_id, codename FROM auth_permission "
                    "ORDER BY content_type_id, codename"
                )
                values.extend(("permission", *row) for row in cursor.fetchall())
            return values

    def _migration_snapshot(self) -> set[tuple[str, str]]:
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT app, name FROM django_migrations")
            return set(cursor.fetchall())

    @staticmethod
    def _json_value(value: Any) -> Any:
        if isinstance(value, (UUID, datetime)):
            return str(value)
        if isinstance(value, list):
            return [CourseMigrationHistoryCompatibilityTests._json_value(item) for item in value]
        if isinstance(value, dict):
            return {
                str(key): CourseMigrationHistoryCompatibilityTests._json_value(item)
                for key, item in value.items()
            }
        return value
