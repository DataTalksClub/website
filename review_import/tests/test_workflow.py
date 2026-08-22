from __future__ import annotations

import errno
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import traceback
import uuid
import warnings
from collections.abc import Callable
from contextlib import closing, redirect_stderr, redirect_stdout
from gc import collect as collect_garbage
from io import StringIO
from pathlib import Path
from typing import Any, ClassVar, Literal, cast
from unittest import TestCase, mock

import review_import.workflow as workflow
from review_import.manifest import ALLOWLIST
from review_import.workflow import (
    ImportConfig,
    ImportFailure,
    ReviewImporter,
    _migrate_fresh_database,
    _readonly_connection,
    _scrub_sensitive_rows,
    _writable_connection,
    cleanup_snapshot,
    fingerprint,
)
from scripts import load_rds_export

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEST_RUN_ID = f"{os.getpid()}-{uuid.uuid4().hex}"
TEST_ROOT = PROJECT_ROOT / ".tmp" / f"issue-99-synthetic-tests-{TEST_RUN_ID}"

CANARIES = {
    "email": "private-person-99@example.test",
    "name": "PRIVATE-NAME-CANARY-99",
    "social": "SOCIAL-UID-CANARY-99",
    "payload": "PROVIDER-PAYLOAD-CANARY-99",
    "session": "SESSION-CANARY-99",
    "token": "TOKEN-CANARY-99",
    "password": "PASSWORD-CANARY-99",
    "free_text": "LEARNER-FREE-TEXT-CANARY-99",
    "learner_url": "https://learner.example.test/PRIVATE-URL-CANARY-99",
    "commit": "PRIVATE-COMMIT-CANARY-99",
    "rendered": "RENDERED-MESSAGE-CANARY-99",
    "error": "PROVIDER-ERROR-CANARY-99",
    "leaderboard": "WRAPPED-IDENTITY-CANARY-99",
}

HOSTILE_PROVIDER_ENVIRONMENT = {
    "DATAMAILER_URL": "https://provider.invalid",
    "DATAMAILER_API_KEY": "synthetic-provider-key",
    "DATAMAILER_CLIENT": "synthetic-client",
    "DATAMAILER_AUDIENCE": "synthetic-audience",
    "DATAMAILER_FROM_EMAIL": "synthetic-sender",
    "DATAMAILER_STRICT": "1",
    "DATAMAILER_TIMEOUT_SECONDS": "90",
    "DATAMAILER_TRANSACTIONAL_DRY_RUN": "0",
    "DATAMAILER_WEBHOOK_TOKEN": "synthetic-webhook-token",
    "DATAMAILER_IMPORT_S3_BUCKET": "synthetic-import-bucket",
    "DATAMAILER_IMPORT_S3_PREFIX": "synthetic-import-prefix",
    "DATAMAILER_IMPORT_URL_EXPIRES_SECONDS": "900",
    "DATAMAILER_IMPORT_S3_REGION": "synthetic-region",
    "DATAMAILER_SYNC_ON_USER_CREATE": "1",
    "DATAMAILER_OUTBOX_DISPATCH_IMMEDIATELY": "1",
    "AWS_ACCESS_KEY_ID": "synthetic-access-key",
    "AWS_SECRET_ACCESS_KEY": "synthetic-secret-key",
    "AWS_SESSION_TOKEN": "synthetic-session-token",
    "AWS_PROFILE": "synthetic-profile",
    "AWS_DEFAULT_PROFILE": "synthetic-default-profile",
    "AWS_WEB_IDENTITY_TOKEN_FILE": "/synthetic/token",
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": "/synthetic/credentials",
    "AWS_CONTAINER_CREDENTIALS_FULL_URI": "http://provider.invalid/credentials",
    "AWS_REGION": "synthetic-region",
    "AWS_DEFAULT_REGION": "synthetic-default-region",
    "CLOUDWATCH_APP_METRIC_REGION": "synthetic-cloudwatch-region",
    "EMAIL_HOST": "smtp.provider.invalid",
    "EMAIL_HOST_USER": "synthetic-smtp-user",
    "EMAIL_HOST_PASSWORD": "synthetic-smtp-password",
}


class TrackableSQLiteConnection:
    fail_at: str | None
    closed: bool
    close_calls: int

    def __init__(self, fail_at: str | None = None) -> None:
        object.__setattr__(self, "fail_at", fail_at)
        object.__setattr__(self, "closed", False)
        object.__setattr__(self, "close_calls", 0)

    def __setattr__(self, name: str, value: object) -> None:
        if name == "row_factory" and self.fail_at == "row_factory":
            raise RuntimeError("synthetic row factory failure")
        object.__setattr__(self, name, value)

    def create_function(self, *_args: object, **_kwargs: object) -> None:
        if self.fail_at == "create_function":
            raise RuntimeError("synthetic function setup failure")

    def execute(self, _query: str) -> TrackableSQLiteConnection:
        if self.fail_at == "pragma":
            raise RuntimeError("synthetic pragma failure")
        return self

    def __enter__(self) -> TrackableSQLiteConnection:
        return self

    def __exit__(self, *_args: object) -> Literal[False]:
        return False

    def close(self) -> None:
        object.__setattr__(self, "closed", True)
        object.__setattr__(self, "close_calls", self.close_calls + 1)


def _insert(connection: sqlite3.Connection, table: str, values: dict[str, object]) -> None:
    columns = tuple(values)
    column_sql = ", ".join(f'"{column}"' for column in columns)
    placeholders = ", ".join("?" for _column in columns)
    connection.execute(
        f'INSERT INTO "{table}" ({column_sql}) VALUES ({placeholders})',
        tuple(values[column] for column in columns),
    )


def _minimal_value(column: sqlite3.Row) -> object:
    column_type = str(column["type"]).upper()
    if "INT" in column_type:
        return 0
    if any(numeric in column_type for numeric in ("REAL", "FLOA", "DOUB", "NUM")):
        return 0.0
    if "BLOB" in column_type:
        return b"x"
    return "synthetic"


def _insert_forbidden_row(
    connection: sqlite3.Connection,
    table: str,
    overrides: dict[str, object],
) -> None:
    connection.row_factory = sqlite3.Row
    values: dict[str, object] = {}
    for column in connection.execute(f'PRAGMA table_info("{table}")').fetchall():
        name = str(column["name"])
        if name in overrides:
            values[name] = overrides[name]
        elif column["pk"]:
            values[name] = 9001 if "INT" in str(column["type"]).upper() else "synthetic-key"
        elif column["notnull"] and column["dflt_value"] is None:
            values[name] = _minimal_value(column)
    for name, value in overrides.items():
        values.setdefault(name, value)
    _insert(connection, table, values)


def _stats_row(table: str, relation_column: str) -> dict[str, object]:
    values: dict[str, object] = {}
    for column in ALLOWLIST[table]:
        if column == "id":
            values[column] = 1
        elif column == relation_column:
            values[column] = 1
        elif column == "total_submissions":
            values[column] = 12
        elif column == "last_calculated":
            values[column] = "2026-08-08T10:00:00+00:00"
        else:
            values[column] = None
    return values


def retained_private_files() -> list[str]:
    return sorted(
        path.relative_to(workflow.PRIVATE_ROOT).as_posix()
        for path in workflow.PRIVATE_ROOT.rglob("*")
        if path.is_file()
    )


def seed_synthetic_snapshot(path: Path) -> None:
    shutil.copy2(TEST_ROOT / "migrated-base.sqlite3", path)
    path.chmod(0o600)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.create_function(
        "REGEXP",
        2,
        lambda expression, value: bool(value and re.search(expression, str(value))),
        deterministic=True,
    )
    connection.execute("PRAGMA foreign_keys=OFF")

    _insert(
        connection,
        "courses_course_family",
        {
            "id": "00000000000000000000000000000001",
            "slug": "data-engineering",
            "title": "Data Engineering Zoomcamp",
            "description": "Data engineering course family.",
            "outcome": "Build reliable data systems.",
            "github_repo_url": "https://github.com/DataTalksClub/data-engineering-zoomcamp",
            "docs_url": "",
            "faq_document_url": "",
            "social_media_hashtag": "#dezoomcamp",
            "visible": 1,
        },
    )
    _insert(
        connection,
        "courses_course_family",
        {
            "id": "00000000000000000000000000000002",
            "slug": "ml-zoomcamp",
            "title": "Machine Learning Zoomcamp",
            "description": "Machine learning course family.",
            "outcome": "Train practical machine learning models.",
            "github_repo_url": "https://github.com/DataTalksClub/machine-learning-zoomcamp",
            "docs_url": "",
            "faq_document_url": "",
            "social_media_hashtag": "#mlzoomcamp",
            "visible": 1,
        },
    )
    _insert(
        connection,
        "courses_course",
        {
            "id": 1,
            "slug": "data-engineering-2026",
            "uuid": "00000000000000000000000000000011",
            "identifier": "2026",
            "year": 2026,
            "title": "Data Engineering Zoomcamp 2026",
            "description": "Current public course content.",
            "start_date": "2026-01-12",
            "end_date": "2026-04-20",
            "registration_url": "https://datatalks.club/courses/data-engineering-2026/",
            "github_repo_url": "https://github.com/DataTalksClub/data-engineering-zoomcamp",
            "social_media_hashtag": "#dezoomcamp",
            "first_homework_scored": 1,
            "finished": 0,
            "faq_document_url": "https://datatalks.club/faq/data-engineering-zoomcamp.html",
            "min_projects_to_pass": 1,
            "homework_problems_comments_field": 1,
            "project_passing_score": 7,
            "visible": 1,
            "course_id": "00000000000000000000000000000001",
        },
    )
    _insert(
        connection,
        "courses_course",
        {
            "id": 2,
            "slug": "ml-zoomcamp-2024",
            "uuid": "00000000000000000000000000000012",
            "identifier": "2024",
            "year": 2024,
            "title": "Machine Learning Zoomcamp 2024",
            "description": "Archived public course content.",
            "start_date": "2024-09-01",
            "end_date": "2024-12-15",
            "registration_url": "",
            "github_repo_url": "https://github.com/DataTalksClub/machine-learning-zoomcamp",
            "social_media_hashtag": "#mlzoomcamp",
            "first_homework_scored": 1,
            "finished": 1,
            "faq_document_url": "",
            "min_projects_to_pass": 2,
            "homework_problems_comments_field": 1,
            "project_passing_score": 10,
            "visible": 1,
            "course_id": "00000000000000000000000000000002",
        },
    )
    _insert(
        connection,
        "courses_registrationcampaign",
        {
            "id": 1,
            "slug": "data-engineering-2026",
            "title": "Data Engineering Zoomcamp registration",
            "edition_label": "2026 cohort",
            "current_course_id": 1,
            "is_active": 1,
            "marketing_markdown": "Join the public course.",
            "meta_description": "Registration information.",
            "hero_image_url": "https://datatalks.club/images/courses/de-zoomcamp.png",
            "video_url": "https://www.youtube.com/watch?v=example",
            "created_at": "2026-01-01T10:00:00+00:00",
            "updated_at": "2026-01-02T10:00:00+00:00",
        },
    )
    _insert(
        connection,
        "courses_homework",
        {
            "id": 1,
            "slug": "module-1",
            "course_id": 1,
            "title": "Module 1 homework",
            "description": "Public homework description.",
            "instructions_url": "https://github.com/DataTalksClub/example/blob/main/homework.md",
            "due_date": "2026-01-20T22:00:00+00:00",
            "learning_in_public_cap": 7,
            "homework_url_field": 1,
            "time_spent_lectures_field": 1,
            "time_spent_homework_field": 1,
            "faq_contribution_field": 1,
            "state": "OP",
            "instructions_markdown": "Public homework instructions.",
        },
    )
    _insert(
        connection,
        "courses_question",
        {
            "id": 1,
            "homework_id": 1,
            "text": "Which answer is correct?",
            "question_type": "MC",
            "answer_type": "",
            "possible_answers": "First\nSecond",
            "correct_answer": "1",
            "scores_for_correct_answer": 1,
        },
    )
    _insert(
        connection,
        "courses_homeworkstatistics",
        _stats_row("courses_homeworkstatistics", "homework_id"),
    )
    _insert(
        connection,
        "courses_project",
        {
            "id": 1,
            "course_id": 1,
            "slug": "capstone",
            "title": "Capstone project",
            "description": "Public project description.",
            "instructions_url": "https://github.com/DataTalksClub/example/blob/main/project.md",
            "submission_due_date": "2026-04-01T22:00:00+00:00",
            "learning_in_public_cap_project": 14,
            "peer_review_due_date": "2026-04-08T22:00:00+00:00",
            "time_spent_project_field": 1,
            "problems_comments_field": 1,
            "faq_contribution_field": 1,
            "learning_in_public_cap_review": 2,
            "number_of_peers_to_evaluate": 3,
            "points_for_peer_review": 3,
            "time_spent_evaluation_field": 1,
            "state": "CS",
        },
    )
    _insert(
        connection,
        "courses_reviewcriteria",
        {
            "id": 1,
            "course_id": 1,
            "description": "Project quality",
            "options": json.dumps(
                [
                    {"criteria": "Needs work", "score": 0},
                    {"criteria": "Complete", "score": 1},
                ]
            ),
            "review_criteria_type": "RB",
        },
    )
    _insert(
        connection,
        "courses_projectstatistics",
        _stats_row("courses_projectstatistics", "project_id"),
    )
    _insert(
        connection,
        "courses_wrappedstatistics",
        {
            "id": 1,
            "year": 2026,
            "is_visible": 1,
            "total_participants": 100,
            "total_enrollments": 120,
            "total_hours": 450.5,
            "total_certificates": 40,
            "total_points": 9000,
            "course_stats": json.dumps(
                [
                    {
                        "title": "Data Engineering Zoomcamp 2026",
                        "slug": "data-engineering-2026",
                        "enrollment_count": 120,
                    }
                ]
            ),
            "leaderboard": json.dumps([{"display_name": CANARIES["leaderboard"]}]),
            "calculated_at": "2026-08-08T10:00:00+00:00",
            "created_at": "2026-08-08T10:00:00+00:00",
        },
    )

    forbidden_rows: tuple[tuple[str, dict[str, object]], ...] = (
        (
            "accounts_customuser",
            {
                "id": 9001,
                "password": CANARIES["password"],
                "first_name": CANARIES["name"],
                "email": CANARIES["email"],
                "is_staff": 1,
            },
        ),
        ("accounts_token", {"key": CANARIES["token"], "user_id": 9001}),
        (
            "socialaccount_socialaccount",
            {
                "id": 9001,
                "user_id": 9001,
                "provider": "github",
                "uid": CANARIES["social"],
                "extra_data": json.dumps({"canary": CANARIES["payload"]}),
            },
        ),
        (
            "django_session",
            {"session_key": CANARIES["session"], "session_data": CANARIES["token"]},
        ),
        (
            "courses_courseregistration",
            {
                "id": 9001,
                "campaign_id": 1,
                "course_id": 1,
                "user_id": 9001,
                "email": CANARIES["email"],
                "email_normalized": CANARIES["email"],
                "name": CANARIES["name"],
                "comment": CANARIES["free_text"],
            },
        ),
        (
            "courses_submission",
            {
                "id": 9001,
                "homework_link": CANARIES["learner_url"],
                "problems_comments": CANARIES["free_text"],
            },
        ),
        (
            "courses_projectsubmission",
            {
                "id": 9001,
                "github_link": CANARIES["learner_url"],
                "commit_id": CANARIES["commit"],
                "problems_comments": CANARIES["free_text"],
            },
        ),
        (
            "courses_peerreview",
            {
                "id": 9001,
                "note_to_peer": CANARIES["free_text"],
                "learning_in_public_links": json.dumps([CANARIES["learner_url"]]),
            },
        ),
        (
            "data_datamaileroutboxevent",
            {
                "id": 9001,
                "payload": json.dumps({"body": CANARIES["rendered"]}),
                "response_payload": json.dumps({"canary": CANARIES["payload"]}),
                "last_error": CANARIES["error"],
            },
        ),
        (
            "jobs_durablejob",
            {
                "id": "9001",
                "payload": json.dumps({"canary": CANARIES["payload"]}),
                "last_error_code": CANARIES["error"],
                "max_attempts": 1,
                "status": "pending",
                "claimed_by": "",
            },
        ),
    )
    for table, overrides in forbidden_rows:
        _insert_forbidden_row(connection, table, overrides)
    connection.commit()
    connection.close()


def fault_at(expected_stage: str) -> Callable[[str], None]:
    def fail(actual_stage: str) -> None:
        if actual_stage == expected_stage:
            raise RuntimeError("synthetic interruption")

    return fail


def lock_boundary_error(
    kind: Literal["os", "runtime"],
    canary: str,
    raw_path: Path,
) -> OSError | RuntimeError:
    if kind == "os":
        return PermissionError(errno.EACCES, canary, str(raw_path))
    return RuntimeError(f"{canary}: {raw_path}")


class ReviewImportWorkflowTests(TestCase):
    original_workflow_paths: ClassVar[dict[str, Path]]

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.original_workflow_paths = {
            name: getattr(workflow, name)
            for name in (
                "PRIVATE_ROOT",
                "ARTIFACTS_DIR",
                "REPORTS_DIR",
                "WORK_DIR",
            )
        }
        private_root = TEST_ROOT / "review-data"
        workflow.PRIVATE_ROOT = private_root
        workflow.ARTIFACTS_DIR = private_root / "artifacts"
        workflow.REPORTS_DIR = private_root / "reports"
        workflow.WORK_DIR = private_root / "work"
        if TEST_ROOT.exists():
            shutil.rmtree(TEST_ROOT)
        TEST_ROOT.mkdir(mode=0o700, parents=True)
        _migrate_fresh_database(TEST_ROOT / "migrated-base.sqlite3")

    @classmethod
    def tearDownClass(cls) -> None:
        if TEST_ROOT.exists():
            shutil.rmtree(TEST_ROOT)
        for name, value in cls.original_workflow_paths.items():
            setattr(workflow, name, value)
        super().tearDownClass()

    def setUp(self) -> None:
        # Finalize resources retained by earlier tests before this case starts its
        # test-local warning window. Parallel worker assignment must not make a
        # previous case's late ResourceWarning look like this workflow leaked it.
        collect_garbage()
        self.warning_context = warnings.catch_warnings(record=True)
        self.caught_warnings = self.warning_context.__enter__()
        warnings.simplefilter("always", ResourceWarning)
        self.previous_environment = os.environ.get("DTC_ENVIRONMENT")
        os.environ["DTC_ENVIRONMENT"] = "test"
        self.case_id = uuid.uuid4().hex
        self.case_dir = TEST_ROOT / self.case_id
        self.case_dir.mkdir(mode=0o700)
        self.source = self.case_dir / "synthetic-source.sqlite3"
        self.target = self.case_dir / "review.sqlite3"
        seed_synthetic_snapshot(self.source)
        self.snapshot_id = f"synthetic-{self.case_id}"

    def tearDown(self) -> None:
        if self.previous_environment is None:
            os.environ.pop("DTC_ENVIRONMENT", None)
        else:
            os.environ["DTC_ENVIRONMENT"] = self.previous_environment
        if self.case_dir.exists():
            shutil.rmtree(self.case_dir)
        cleanup_snapshot(self.snapshot_id)
        collect_garbage()
        resource_warnings = [
            warning
            for warning in self.caught_warnings
            if issubclass(warning.category, ResourceWarning)
        ]
        self.warning_context.__exit__(None, None, None)
        if resource_warnings:
            self.fail(f"unclosed resource warnings: {len(resource_warnings)}")

    def config(self, **overrides: object) -> ImportConfig:
        values: dict[str, object] = {
            "source_db": self.source,
            "snapshot_id": self.snapshot_id,
            "target_db": self.target,
            "allow_repo_source_for_tests": True,
        }
        values.update(overrides)
        return ImportConfig(**values)  # type: ignore[arg-type]

    def assert_safe_lock_failure(
        self,
        error: ImportFailure,
        *,
        category: str,
        canaries: tuple[str, ...],
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        self.assertEqual(error.category, category)
        self.assertIsNone(error.__context__)
        self.assertIsNone(error.__cause__)
        self.assertTrue(error.__suppress_context__)
        rendered = "\n".join(
            (
                stdout,
                stderr,
                str(error),
                repr(error),
                repr(error.__context__),
                "".join(traceback.format_exception(error)),
            )
        )
        for canary in canaries:
            self.assertNotIn(canary, rendered)

    def capture_operation_lock_failure(self) -> tuple[ImportFailure, str, str]:
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                with workflow._operation_lock():
                    self.fail("unsafe lock boundary unexpectedly yielded")
            except ImportFailure as error:
                return error, stdout.getvalue(), stderr.getvalue()
        self.fail("unsafe lock boundary did not fail")

    def capture_lock_failure_with_ambient(
        self,
        ambient: OSError | RuntimeError,
    ) -> tuple[ImportFailure, str, str]:
        try:
            raise ambient
        except (OSError, RuntimeError):
            return self.capture_operation_lock_failure()

    def run_from_migrated_baseline(
        self,
        config: ImportConfig,
        *,
        fault_hook: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        def copy_migrated_database(destination: Path) -> None:
            shutil.copy2(TEST_ROOT / "migrated-base.sqlite3", destination)
            destination.chmod(0o600)

        importer = ReviewImporter(fault_hook=fault_hook or (lambda _stage: None))
        with (
            mock.patch.object(
                workflow,
                "_migrate_fresh_database",
                side_effect=copy_migrated_database,
            ),
            mock.patch.object(workflow, "_run_django"),
            mock.patch.object(workflow, "_assert_public_review_pages"),
        ):
            return importer.run(config)

    def test_apply_is_repeatable_and_excludes_every_canary(self) -> None:
        before = fingerprint(self.source)
        first = ReviewImporter().run(self.config())
        second = self.run_from_migrated_baseline(self.config())

        self.assertEqual(before, fingerprint(self.source))
        self.assertEqual(first["table_counts"], second["table_counts"])
        self.assertEqual(first["relationship_counts"], second["relationship_counts"])
        self.assertEqual(first["logical_checksum"], second["logical_checksum"])
        self.assertEqual(first["synthetic_admin_count"], 1)

        artifact = workflow.ARTIFACTS_DIR / f"{self.snapshot_id}.sqlite3"
        report = workflow.REPORTS_DIR / f"{self.snapshot_id}.json"
        derived_payloads = (
            artifact.read_bytes(),
            self.target.read_bytes(),
            report.read_bytes(),
            json.dumps(first, sort_keys=True).encode(),
        )
        for label, canary in CANARIES.items():
            with self.subTest(canary=label):
                encoded = canary.encode()
                self.assertTrue(all(encoded not in payload for payload in derived_payloads))

        report_data = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(report_data["source_size"], before.size)
        self.assertEqual(report_data["source_sha256"], before.sha256)
        self.assertEqual(report_data["allowlist_schema_version"], "cmp-public-review-v1")
        self.assertNotIn(str(self.source), report.read_text(encoding="utf-8"))
        self.assertNotIn("admin_password", report.read_text(encoding="utf-8"))
        self.assertFalse(any(report_data["source_origin_denylist_zero_counts"].values()))

        with closing(sqlite3.connect(self.target)) as connection:
            users = connection.execute(
                "SELECT email, is_staff, is_superuser FROM accounts_customuser"
            ).fetchall()
            self.assertEqual(users, [("review-admin@example.invalid", 1, 1)])
            self.assertEqual(
                connection.execute(
                    """
                    SELECT auth_group.name
                    FROM auth_group
                    JOIN accounts_customuser_groups
                      ON accounts_customuser_groups.group_id = auth_group.id
                    JOIN accounts_customuser
                      ON accounts_customuser.id = accounts_customuser_groups.customuser_id
                    WHERE accounts_customuser.email = ?
                    """,
                    ("review-admin@example.invalid",),
                ).fetchall(),
                [("course_operator",)],
            )
            self.assertEqual(
                json.loads(
                    connection.execute(
                        "SELECT leaderboard FROM courses_wrappedstatistics"
                    ).fetchone()[0]
                ),
                [],
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM courses_courseregistration").fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM courses_submission").fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM data_datamaileroutboxevent").fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT seq FROM sqlite_sequence WHERE name = 'courses_course'"
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT slug, title FROM courses_course_family ORDER BY slug"
                ).fetchall(),
                [
                    ("data-engineering", "Data Engineering Zoomcamp"),
                    ("ml-zoomcamp", "Machine Learning Zoomcamp"),
                ],
            )
            self.assertEqual(
                connection.execute(
                    """
                    SELECT cohort.slug, family.slug, cohort.identifier, cohort.year
                    FROM courses_course AS cohort
                    JOIN courses_course_family AS family ON family.id = cohort.course_id
                    ORDER BY cohort.id
                    """
                ).fetchall(),
                [
                    ("data-engineering-2026", "data-engineering", "2026", 2026),
                    ("ml-zoomcamp-2024", "ml-zoomcamp", "2024", 2024),
                ],
            )

        self.assertEqual(artifact.stat().st_mode & 0o777, 0o600)
        self.assertEqual(report.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(artifact.parent.stat().st_mode & 0o777, 0o700)
        ignored = subprocess.run(
            ["git", "check-ignore", "--quiet", "--", str(self.target)],
            cwd=PROJECT_ROOT,
            check=False,
        )
        self.assertEqual(ignored.returncode, 0)

    def test_fresh_scrub_removes_event_dependencies_with_foreign_keys_enabled(self) -> None:
        database = self.case_dir / "dependency-scrub.sqlite3"
        shutil.copy2(TEST_ROOT / "migrated-base.sqlite3", database)
        database.chmod(0o600)

        with _writable_connection(database) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM events_event").fetchone()[0],
                0,
            )
            event_id = uuid.uuid4().hex
            _insert(
                connection,
                "events_event",
                {
                    "id": event_id,
                    "title": "Synthetic dependency event",
                    "slug": "synthetic-dependency-event",
                    "lifecycle": "published",
                    "source_repository": "synthetic-repository",
                    "source_revision": "synthetic-revision",
                    "source_key": "synthetic-event",
                    "source_path": "/events/synthetic-dependency-event",
                    "source_checksum": "a" * 64,
                    "created_at": "2026-08-12T00:00:00+00:00",
                    "updated_at": "2026-08-12T00:00:00+00:00",
                },
            )
            _insert(
                connection,
                "events_eventalias",
                {
                    "id": uuid.uuid4().hex,
                    "source_path": "/events/synthetic-dependency-alias",
                    "kind": "reviewed",
                    "reason": "Synthetic dependency fixture",
                    "source_repository": "synthetic-repository",
                    "source_revision": "synthetic-revision",
                    "source_key": "synthetic-event",
                    "activated_at": "2026-08-12T00:00:00+00:00",
                    "event_id": event_id,
                },
            )
            connection.execute(
                """
                CREATE TABLE review_import_synthetic_dependency (
                    id INTEGER PRIMARY KEY,
                    event_id CHAR(32),
                    FOREIGN KEY (event_id) REFERENCES events_event(id)
                )
                """
            )
            connection.execute(
                "INSERT INTO review_import_synthetic_dependency (event_id) VALUES (?)",
                (event_id,),
            )

            _scrub_sensitive_rows(connection)

            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM events_event").fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM events_eventalias").fetchone()[0],
                0,
            )
            self.assertIsNone(
                connection.execute(
                    "SELECT event_id FROM review_import_synthetic_dependency"
                ).fetchone()[0]
            )
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_default_root_apply_retains_exactly_three_files(self) -> None:
        target = workflow.PRIVATE_ROOT / "review.sqlite3"
        config = self.config(target_db=target)

        self.run_from_migrated_baseline(config)

        self.assertEqual(
            retained_private_files(),
            [
                f"artifacts/{self.snapshot_id}.sqlite3",
                f"reports/{self.snapshot_id}.json",
                "review.sqlite3",
            ],
        )
        self.assertEqual(
            cleanup_snapshot(
                self.snapshot_id,
                include_target=True,
                target_db=target,
            ),
            {"sanitized": 1, "report": 1, "target": 1},
        )
        self.assertEqual(retained_private_files(), [])

    def test_provider_environment_cannot_reactivate_admin_side_effects(self) -> None:
        def bootstrap_with_failing_transports(path: Path, password: str) -> None:
            environment = workflow._django_environment(path)
            environment["REVIEW_ADMIN_PASSWORD"] = password
            code = """
import os
from unittest.mock import patch

import django

django.setup()

from django.conf import settings
from course_management.datamailer.client import DatamailerConfig
from data.models import DatamailerOutboxEvent
from jobs.models import DurableJob
from review_import.admin import create_synthetic_admin

empty_settings = (
    'DATAMAILER_URL',
    'DATAMAILER_API_KEY',
    'DATAMAILER_CLIENT',
    'DATAMAILER_AUDIENCE',
    'DATAMAILER_FROM_EMAIL',
    'DATAMAILER_WEBHOOK_TOKEN',
    'DATAMAILER_IMPORT_S3_BUCKET',
    'DATAMAILER_IMPORT_S3_PREFIX',
    'DATAMAILER_IMPORT_S3_REGION',
)
if any(getattr(settings, name) for name in empty_settings):
    raise SystemExit(10)
if settings.DATAMAILER_SYNC_ON_USER_CREATE:
    raise SystemExit(11)
if settings.DATAMAILER_OUTBOX_DISPATCH_IMMEDIATELY:
    raise SystemExit(12)
if not settings.DATAMAILER_TRANSACTIONAL_DRY_RUN:
    raise SystemExit(13)
if settings.DATAMAILER_STRICT:
    raise SystemExit(14)
if settings.DATAMAILER_TIMEOUT_SECONDS != 0.0:
    raise SystemExit(15)
if settings.DATAMAILER_IMPORT_URL_EXPIRES_SECONDS != 0:
    raise SystemExit(16)
if settings.EMAIL_BACKEND != 'django.core.mail.backends.dummy.EmailBackend':
    raise SystemExit(17)
if not settings.Q_CLUSTER.get('sync') or settings.Q_CLUSTER.get('scheduler'):
    raise SystemExit(18)
if DatamailerConfig.from_settings() is not None:
    raise SystemExit(19)

with (
    patch('courses.signals.sync_contact', side_effect=AssertionError('sync attempted')) as sync,
    patch(
        'course_management.datamailer.client.DatamailerClient.request',
        side_effect=AssertionError('provider transport attempted'),
    ) as transport,
    patch(
        'django.core.mail.message.EmailMessage.send',
        side_effect=AssertionError('email attempted'),
    ) as email_send,
    patch(
        'django_q.tasks.async_task',
        side_effect=AssertionError('job wakeup attempted'),
    ) as async_task,
):
    create_synthetic_admin(os.environ['REVIEW_ADMIN_PASSWORD'])

if sync.called or transport.called or email_send.called or async_task.called:
    raise SystemExit(20)
if DatamailerOutboxEvent.objects.count() != 0:
    raise SystemExit(21)
if DurableJob.objects.count() != 0:
    raise SystemExit(22)
"""
            result = subprocess.run(
                [sys.executable, "-c", code],
                cwd=PROJECT_ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

        with (
            mock.patch.dict(os.environ, HOSTILE_PROVIDER_ENVIRONMENT),
            mock.patch.object(
                workflow,
                "_create_synthetic_admin",
                side_effect=bootstrap_with_failing_transports,
            ),
        ):
            child_environment = workflow._django_environment(self.target)
            for name in HOSTILE_PROVIDER_ENVIRONMENT:
                with self.subTest(environment=name):
                    self.assertNotEqual(
                        child_environment[name],
                        HOSTILE_PROVIDER_ENVIRONMENT[name],
                    )
            report = self.run_from_migrated_baseline(self.config())

        self.assertEqual(report["synthetic_admin_count"], 1)
        with closing(sqlite3.connect(self.target)) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM accounts_customuser WHERE email = ?",
                    ("review-admin@example.invalid",),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM data_datamaileroutboxevent").fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM jobs_durablejob").fetchone()[0],
                0,
            )

    def test_local_review_browsing_never_constructs_network_clients(self) -> None:
        with mock.patch.dict(os.environ, HOSTILE_PROVIDER_ENVIRONMENT):
            self.run_from_migrated_baseline(self.config())

        environment = os.environ.copy()
        environment.update(HOSTILE_PROVIDER_ENVIRONMENT)
        environment.update(
            {
                "DJANGO_SETTINGS_MODULE": "website.settings.local_review",
                "DTC_ENVIRONMENT": "local",
                "DTC_USE_SQLITE": "true",
                "DTC_SQLITE_PATH": str(self.target),
            }
        )
        code = """
import os
from unittest.mock import patch

import django

django.setup()

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client
from review_import.environment import LOCAL_REVIEW_PROVIDER_ENVIRONMENT

if not settings.LOCAL_REVIEW_OUTBOUND_NETWORK_DISABLED:
    raise SystemExit(30)
if settings.CLOUDWATCH_APP_METRIC_REGION or settings.AWS_REGION or settings.AWS_DEFAULT_REGION:
    raise SystemExit(31)
for name, expected in LOCAL_REVIEW_PROVIDER_ENVIRONMENT.items():
    if os.environ.get(name) != expected:
        raise SystemExit(32)

client = Client()
user = get_user_model().objects.get(email='review-admin@example.invalid')
if set(user.groups.values_list('name', flat=True)) != {'course_operator'}:
    raise SystemExit(33)
client.force_login(user)
ordinary_paths = (
    '/courses/',
    '/courses/data-engineering-2026/',
    '/courses/ml-zoomcamp-2024/',
    '/courses/data-engineering-2026/homework/module-1',
    '/courses/data-engineering-2026/homework/module-1/stats',
    '/courses/data-engineering-2026/project/capstone',
    '/courses/data-engineering-2026/project/capstone/stats',
    '/courses/register/data-engineering-2026/',
    '/courses/wrapped/2026/',
)

with (
    patch('boto3.client', side_effect=AssertionError('boto client attempted')) as boto,
    patch(
        'botocore.session.Session.create_client',
        side_effect=AssertionError('botocore client attempted'),
    ) as botocore,
    patch(
        'requests.sessions.Session.request',
        side_effect=AssertionError('HTTP provider attempted'),
    ) as requests_transport,
    patch(
        'course_management.datamailer.client.DatamailerClient.request',
        side_effect=AssertionError('Datamailer attempted'),
    ) as datamailer,
    patch(
        'socket.create_connection',
        side_effect=AssertionError('socket connection attempted'),
    ) as create_connection,
    patch(
        'socket.socket.connect',
        side_effect=AssertionError('raw socket attempted'),
    ) as socket_connect,
):
    admin_index = client.get('/studio/courses')
    if admin_index.status_code != 200:
        raise SystemExit(34)
    if b'/studio/courses/cloudwatch/' not in admin_index.content:
        raise SystemExit(35)
    if b'/studio/courses/datamailer/' not in admin_index.content:
        raise SystemExit(36)

    cloudwatch = client.get('/studio/courses/cloudwatch/')
    if cloudwatch.status_code != 200 or b'disabled' not in cloudwatch.content.lower():
        raise SystemExit(37)
    if client.get('/studio/courses/datamailer/').status_code != 200:
        raise SystemExit(38)
    if client.get('/accounts/github/login/').status_code != 403:
        raise SystemExit(39)
    if client.post('/studio/courses/datamailer/', {'action': 'requeue'}).status_code != 403:
        raise SystemExit(40)

    legacy_checks = (
        ('/cadmin/?source=review', '/studio/courses?source=review'),
        ('/cadmin/cloudwatch/?source=review', '/studio/courses/cloudwatch/?source=review'),
        ('/cadmin/datamailer/?source=review', '/studio/courses/datamailer/?source=review'),
    )
    for legacy, canonical in legacy_checks:
        response = client.get(legacy)
        if response.status_code != 302 or response.headers.get('Location') != canonical:
            raise SystemExit(41)
    legacy_post = client.post('/cadmin/datamailer/?source=review', {'action': 'requeue'})
    if legacy_post.status_code != 403 or legacy_post.headers.get('Location'):
        raise SystemExit(42)
    for path in ordinary_paths:
        if client.get(path, follow=True).status_code != 200:
            raise SystemExit(43)

if any(
    probe.called
    for probe in (
        boto,
        botocore,
        requests_transport,
        datamailer,
        create_connection,
        socket_connect,
    )
):
    raise SystemExit(44)
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=PROJECT_ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_dry_run_twice_leaves_all_published_files_unchanged(self) -> None:
        artifact = workflow.ARTIFACTS_DIR / f"{self.snapshot_id}.sqlite3"
        report = workflow.REPORTS_DIR / f"{self.snapshot_id}.json"
        artifact.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        report.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        artifact.write_bytes(b"existing-artifact")
        report.write_bytes(b"existing-report")
        with closing(sqlite3.connect(self.target)) as connection, connection:
            connection.execute("CREATE TABLE sentinel (id INTEGER PRIMARY KEY)")
            connection.execute("INSERT INTO sentinel(id) VALUES (1)")
        target_before = fingerprint(self.target)
        before_source = fingerprint(self.source)

        first = self.run_from_migrated_baseline(self.config(dry_run=True))
        second = self.run_from_migrated_baseline(self.config(dry_run=True))

        self.assertEqual(first["logical_checksum"], second["logical_checksum"])
        self.assertEqual(first["table_counts"], second["table_counts"])
        self.assertEqual(first["synthetic_admin_count"], 0)
        self.assertEqual(artifact.read_bytes(), b"existing-artifact")
        self.assertEqual(report.read_bytes(), b"existing-report")
        self.assertEqual(fingerprint(self.target), target_before)
        self.assertEqual(fingerprint(self.source), before_source)
        self.assertEqual(
            retained_private_files(),
            [
                f"artifacts/{self.snapshot_id}.sqlite3",
                f"reports/{self.snapshot_id}.json",
            ],
        )

    def test_invalid_schema_fields_and_relationships_fail_closed(self) -> None:
        mutations = (
            (
                "unknown-table",
                lambda connection: connection.execute(
                    "CREATE TABLE unexpected_private_data (id INTEGER)"
                ),
                "schema-unknown-table",
            ),
            (
                "unknown-column",
                lambda connection: connection.execute(
                    "ALTER TABLE courses_course ADD COLUMN unexpected_private_value TEXT"
                ),
                "schema-unknown-column",
            ),
            (
                "missing-column",
                lambda connection: connection.execute(
                    "ALTER TABLE courses_course DROP COLUMN description"
                ),
                "schema-missing-column",
            ),
            (
                "rubric-json",
                lambda connection: connection.execute(
                    "UPDATE courses_reviewcriteria SET options = ?",
                    (json.dumps([{"criteria": "Valid", "score": 1, "user_id": 9}]),),
                ),
                "field-validation",
            ),
            (
                "wrapped-json",
                lambda connection: connection.execute(
                    "UPDATE courses_wrappedstatistics SET course_stats = ?",
                    (
                        json.dumps(
                            [
                                {
                                    "title": "Course",
                                    "slug": "data-engineering-2026",
                                    "enrollment_count": 1,
                                    "student_email": CANARIES["email"],
                                }
                            ]
                        ),
                    ),
                ),
                "field-validation",
            ),
            (
                "adopted-url-space",
                lambda connection: connection.execute(
                    "UPDATE courses_course SET registration_url = ? WHERE id = 1",
                    ("https://example.test/path with space",),
                ),
                "field-validation",
            ),
            (
                "adopted-url-host",
                lambda connection: connection.execute(
                    "UPDATE courses_course SET registration_url = ? WHERE id = 1",
                    ("https://invalid_host.example.test/path",),
                ),
                "field-validation",
            ),
            (
                "adopted-url-length",
                lambda connection: connection.execute(
                    "UPDATE courses_course SET registration_url = ? WHERE id = 1",
                    ("https://example.test/" + ("a" * 2049),),
                ),
                "field-validation",
            ),
            (
                "credential-url",
                lambda connection: connection.execute(
                    "UPDATE courses_course SET registration_url = ? WHERE id = 1",
                    ("https://example.test/register?api_key=PRIVATE",),
                ),
                "field-validation",
            ),
            (
                "broken-relation",
                lambda connection: connection.execute(
                    "UPDATE courses_question SET homework_id = 999 WHERE id = 1"
                ),
                "broken-relationship",
            ),
        )
        for label, mutation, expected_category in mutations:
            with self.subTest(case=label):
                source = self.case_dir / f"{label}.sqlite3"
                seed_synthetic_snapshot(source)
                with closing(sqlite3.connect(source)) as connection:
                    mutation(connection)
                    connection.commit()
                source_before = fingerprint(source)
                target_before = self.target.read_bytes() if self.target.exists() else None
                config = self.config(
                    source_db=source,
                    snapshot_id=f"{self.snapshot_id}-{label}",
                )
                with self.assertRaises(ImportFailure) as raised:
                    self.run_from_migrated_baseline(config)
                self.assertEqual(raised.exception.category, expected_category)
                self.assertEqual(
                    self.target.read_bytes() if self.target.exists() else None,
                    target_before,
                )
                self.assertEqual(fingerprint(source), source_before)
                cleanup_snapshot(f"{self.snapshot_id}-{label}")

    def test_failure_at_each_stage_preserves_previous_target_and_outputs(self) -> None:
        self.run_from_migrated_baseline(self.config())
        artifact = workflow.ARTIFACTS_DIR / f"{self.snapshot_id}.sqlite3"
        report = workflow.REPORTS_DIR / f"{self.snapshot_id}.json"
        baseline = {
            "target": fingerprint(self.target),
            "artifact": fingerprint(artifact),
            "report": fingerprint(report),
            "source": fingerprint(self.source),
        }

        for stage in ("during-validation", "before-publish", "during-publish"):
            with self.subTest(stage=stage):
                with self.assertRaises(ImportFailure):
                    self.run_from_migrated_baseline(
                        self.config(),
                        fault_hook=fault_at(stage),
                    )
                self.assertEqual(fingerprint(self.target), baseline["target"])
                self.assertEqual(fingerprint(artifact), baseline["artifact"])
                self.assertEqual(fingerprint(report), baseline["report"])
                self.assertEqual(fingerprint(self.source), baseline["source"])
                self.assertEqual(
                    list(workflow.WORK_DIR.glob(f"{self.snapshot_id}-*")),
                    [],
                )

    def test_two_process_contention_preserves_owned_outputs_and_allows_retry(self) -> None:
        workflow._prepare_private_directories()
        artifact = workflow.ARTIFACTS_DIR / f"{self.snapshot_id}.sqlite3"
        report = workflow.REPORTS_DIR / f"{self.snapshot_id}.json"
        artifact.write_bytes(b"owned-artifact")
        report.write_bytes(b"owned-report")
        source_before = fingerprint(self.source)
        ready = self.case_dir / "holder-ready"
        release = self.case_dir / "holder-release"
        holder_code = """
import sys
import time
from pathlib import Path

import review_import.workflow as workflow

root = Path(sys.argv[1])
workflow.PRIVATE_ROOT = root
workflow.ARTIFACTS_DIR = root / 'artifacts'
workflow.REPORTS_DIR = root / 'reports'
workflow.WORK_DIR = root / 'work'
workflow._prepare_private_directories()
with workflow._operation_lock():
    Path(sys.argv[2]).write_text('ready', encoding='utf-8')
    deadline = time.monotonic() + 10
    while not Path(sys.argv[3]).exists():
        if time.monotonic() >= deadline:
            raise SystemExit(2)
        time.sleep(0.01)
"""
        child_environment = os.environ.copy()
        child_environment["DTC_ENVIRONMENT"] = "test"
        holder = subprocess.Popen(
            [
                sys.executable,
                "-c",
                holder_code,
                str(workflow.PRIVATE_ROOT),
                str(ready),
                str(release),
            ],
            cwd=PROJECT_ROOT,
            env=child_environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and holder.poll() is None:
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.01)
            if not ready.exists():
                stdout, stderr = holder.communicate(timeout=1)
                self.fail(f"lock holder did not start: {stdout} {stderr}")

            with self.assertRaises(ImportFailure) as import_contention:
                self.run_from_migrated_baseline(self.config())
            self.assertEqual(import_contention.exception.category, "concurrent-operation")

            with self.assertRaises(ImportFailure) as cleanup_contention:
                cleanup_snapshot(self.snapshot_id)
            self.assertEqual(cleanup_contention.exception.category, "concurrent-operation")
            self.assertEqual(artifact.read_bytes(), b"owned-artifact")
            self.assertEqual(report.read_bytes(), b"owned-report")
            self.assertEqual(
                retained_private_files(),
                [
                    f"artifacts/{self.snapshot_id}.sqlite3",
                    f"reports/{self.snapshot_id}.json",
                ],
            )
            self.assertFalse(self.target.exists())
            self.assertEqual(fingerprint(self.source), source_before)
        finally:
            release.touch()
            try:
                stdout, stderr = holder.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                holder.terminate()
                stdout, stderr = holder.communicate(timeout=5)
                self.fail(f"lock holder did not stop: {stdout} {stderr}")
        self.assertEqual(holder.returncode, 0, stderr)

        self.assertEqual(
            cleanup_snapshot(self.snapshot_id),
            {"sanitized": 1, "report": 1, "target": 0},
        )
        applied = self.run_from_migrated_baseline(self.config())
        self.assertEqual(applied["table_counts"]["courses_course"], 2)
        self.assertEqual(
            retained_private_files(),
            [
                f"artifacts/{self.snapshot_id}.sqlite3",
                f"reports/{self.snapshot_id}.json",
            ],
        )

    def test_cleanup_is_exact_idempotent_and_target_requires_flag(self) -> None:
        self.run_from_migrated_baseline(self.config())
        artifact = workflow.ARTIFACTS_DIR / f"{self.snapshot_id}.sqlite3"
        report = workflow.REPORTS_DIR / f"{self.snapshot_id}.json"

        first = cleanup_snapshot(self.snapshot_id)
        second = cleanup_snapshot(self.snapshot_id)

        self.assertEqual(first, {"sanitized": 1, "report": 1, "target": 0})
        self.assertEqual(second, {"sanitized": 0, "report": 0, "target": 0})
        self.assertFalse(artifact.exists())
        self.assertFalse(report.exists())
        self.assertEqual(retained_private_files(), [])
        self.assertTrue(self.target.exists())
        removed_target = cleanup_snapshot(
            self.snapshot_id,
            include_target=True,
            target_db=self.target,
        )
        self.assertEqual(removed_target["target"], 1)
        self.assertFalse(self.target.exists())

        for unsafe_id in ("../escape", "*", "", "/"):
            with self.subTest(snapshot_id=unsafe_id):
                with self.assertRaises(ImportFailure):
                    cleanup_snapshot(unsafe_id)

    def test_successful_apply_retires_previous_derived_snapshot(self) -> None:
        self.run_from_migrated_baseline(self.config())
        first_artifact = workflow.ARTIFACTS_DIR / f"{self.snapshot_id}.sqlite3"
        first_report = workflow.REPORTS_DIR / f"{self.snapshot_id}.json"
        next_snapshot_id = f"{self.snapshot_id}-next"

        next_report = self.run_from_migrated_baseline(self.config(snapshot_id=next_snapshot_id))

        self.assertFalse(first_artifact.exists())
        self.assertFalse(first_report.exists())
        self.assertTrue((workflow.ARTIFACTS_DIR / f"{next_snapshot_id}.sqlite3").exists())
        self.assertTrue((workflow.REPORTS_DIR / f"{next_snapshot_id}.json").exists())
        self.assertEqual(next_report["table_counts"]["courses_course"], 2)
        cleanup_snapshot(next_snapshot_id)

    def test_source_alias_non_sqlite_target_and_protected_repo_source_are_refused(self) -> None:
        with self.assertRaises(ImportFailure) as protected:
            ReviewImporter().run(
                ImportConfig(
                    source_db=self.source,
                    snapshot_id=self.snapshot_id,
                    target_db=self.target,
                )
            )
        self.assertEqual(protected.exception.category, "protected-source-inside-repository")

        with self.assertRaises(ImportFailure) as alias:
            ReviewImporter().run(self.config(target_db=self.source))
        self.assertEqual(alias.exception.category, "path-alias")

        wrong_suffix = self.case_dir / "review.txt"
        with self.assertRaises(ImportFailure) as suffix:
            ReviewImporter().run(self.config(target_db=wrong_suffix))
        self.assertEqual(suffix.exception.category, "target-not-sqlite")

        invalid_sqlite = self.case_dir / "invalid.sqlite3"
        invalid_sqlite.write_text("not a SQLite database", encoding="utf-8")
        with self.assertRaises(ImportFailure) as invalid:
            ReviewImporter().run(self.config(target_db=invalid_sqlite))
        self.assertEqual(invalid.exception.category, "target-not-sqlite")

    def test_sqlite_connections_close_on_setup_body_and_success_paths(self) -> None:
        helpers = (
            ("readonly", _readonly_connection),
            ("writable", _writable_connection),
        )
        for helper_name, helper in helpers:
            with self.subTest(helper=helper_name, case="success"):
                connection = TrackableSQLiteConnection()
                with mock.patch.object(workflow.sqlite3, "connect", return_value=connection):
                    with helper(self.source) as yielded:
                        self.assertIs(yielded, connection)
                self.assertTrue(connection.closed)
                self.assertEqual(connection.close_calls, 1)

            with self.subTest(helper=helper_name, case="body-failure"):
                connection = TrackableSQLiteConnection()
                with (
                    mock.patch.object(workflow.sqlite3, "connect", return_value=connection),
                    self.assertRaisesRegex(RuntimeError, "synthetic body failure"),
                ):
                    with helper(self.source):
                        raise RuntimeError("synthetic body failure")
                self.assertTrue(connection.closed)
                self.assertEqual(connection.close_calls, 1)

            for failure_point in ("row_factory", "create_function", "pragma"):
                with self.subTest(helper=helper_name, case=failure_point):
                    connection = TrackableSQLiteConnection(failure_point)
                    with (
                        mock.patch.object(
                            workflow.sqlite3,
                            "connect",
                            return_value=connection,
                        ),
                        self.assertRaises(RuntimeError),
                    ):
                        with helper(self.source):
                            self.fail("connection setup unexpectedly yielded")
                    self.assertTrue(connection.closed)
                    self.assertEqual(connection.close_calls, 1)

    def test_source_connection_is_read_only_and_source_change_is_detected(self) -> None:
        source_before = fingerprint(self.source)
        with _readonly_connection(self.source) as connection:
            self.assertEqual(connection.execute("PRAGMA query_only").fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("UPDATE courses_course SET title = 'changed' WHERE id = 1")
        self.assertEqual(fingerprint(self.source), source_before)

        def mutate_source(stage: str) -> None:
            if stage != "during-validation":
                return
            with closing(sqlite3.connect(self.source)) as connection:
                connection.execute(
                    "UPDATE courses_course SET title = 'externally changed' WHERE id = 1"
                )
                connection.commit()

        with self.assertRaises(ImportFailure) as changed:
            self.run_from_migrated_baseline(
                self.config(),
                fault_hook=mutate_source,
            )
        self.assertEqual(changed.exception.category, "source-changed")
        self.assertFalse(self.target.exists())
        self.assertFalse((workflow.ARTIFACTS_DIR / f"{self.snapshot_id}.sqlite3").exists())
        self.assertFalse((workflow.REPORTS_DIR / f"{self.snapshot_id}.json").exists())
        self.assertEqual(list(workflow.WORK_DIR.glob(f"{self.snapshot_id}-*")), [])

    def test_cleanup_refuses_a_derived_symlink_escape(self) -> None:
        symlink_snapshot = f"{self.snapshot_id}-symlink"
        report = workflow.REPORTS_DIR / f"{symlink_snapshot}.json"
        report.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        report.symlink_to(self.source)
        source_before = fingerprint(self.source)
        try:
            with self.assertRaises(ImportFailure) as raised:
                cleanup_snapshot(symlink_snapshot)
            self.assertEqual(raised.exception.category, "unsafe-cleanup-symlink")
            self.assertEqual(fingerprint(self.source), source_before)
        finally:
            report.unlink(missing_ok=True)

    def test_directory_lock_refuses_symlink_and_non_tmp_roots(self) -> None:
        with (
            mock.patch.object(workflow, "PRIVATE_ROOT", PROJECT_ROOT),
            self.assertRaises(ImportFailure) as outside,
        ):
            with workflow._operation_lock():
                self.fail("outside-repository-tmp lock unexpectedly acquired")
        self.assertEqual(outside.exception.category, "unsafe-lock-path")

        symlink_root = self.case_dir / "review-data-link"
        symlink_root.symlink_to(workflow.PRIVATE_ROOT, target_is_directory=True)
        with (
            mock.patch.object(workflow, "PRIVATE_ROOT", symlink_root),
            self.assertRaises(ImportFailure) as symlink,
        ):
            with workflow._operation_lock():
                self.fail("symlink lock unexpectedly acquired")
        self.assertEqual(symlink.exception.category, "unsafe-lock-path")

    def test_lock_resolution_failures_are_redacted_before_descriptor_open(self) -> None:
        workflow._prepare_private_directories()
        before = retained_private_files()
        real_resolved = workflow._resolved
        repository_tmp = workflow.PROJECT_ROOT / ".tmp"
        resolution_cases = (
            ("repository-tmp", 0, repository_tmp),
            ("private-root", 1, workflow.PRIVATE_ROOT),
        )

        def resolution_failure_hook(
            selected_index: int,
            failure: OSError | RuntimeError,
            calls: list[Path],
        ) -> Callable[..., Path]:
            def fail_selected_resolution(
                path: Path,
                *,
                strict: bool = False,
            ) -> Path:
                call_index = len(calls)
                calls.append(path)
                if call_index == selected_index:
                    raise failure
                return real_resolved(path, strict=strict)

            return fail_selected_resolution

        for label, failure_index, raw_path in resolution_cases:
            for error_kind in ("os", "runtime"):
                with self.subTest(path=label, error=error_kind):
                    canary = f"LOCK-RESOLVE-{label}-{error_kind}-CANARY"
                    injected = lock_boundary_error(error_kind, canary, raw_path)
                    resolved_calls: list[Path] = []

                    open_descriptor = mock.Mock()
                    flock = mock.Mock()
                    close = mock.Mock()
                    with (
                        mock.patch.object(
                            workflow,
                            "_resolved",
                            side_effect=resolution_failure_hook(
                                failure_index,
                                injected,
                                resolved_calls,
                            ),
                        ),
                        mock.patch.object(workflow.os, "open", open_descriptor),
                        mock.patch.object(workflow.fcntl, "flock", flock),
                        mock.patch.object(workflow.os, "close", close),
                    ):
                        raised, stdout, stderr = self.capture_operation_lock_failure()

                    self.assert_safe_lock_failure(
                        raised,
                        category="unsafe-lock-path",
                        canaries=(canary, str(raw_path)),
                        stdout=stdout,
                        stderr=stderr,
                    )
                    self.assertEqual(len(resolved_calls), failure_index + 1)
                    open_descriptor.assert_not_called()
                    flock.assert_not_called()
                    close.assert_not_called()

                    with workflow._operation_lock():
                        self.assertEqual(retained_private_files(), before)
                    self.assertEqual(retained_private_files(), before)

    def test_lock_path_predicate_failures_are_redacted_before_descriptor_open(
        self,
    ) -> None:
        workflow._prepare_private_directories()
        before = retained_private_files()
        path_type = type(workflow.PRIVATE_ROOT)
        real_is_symlink = path_type.is_symlink
        real_is_dir = path_type.is_dir
        repository_tmp = workflow.PROJECT_ROOT / ".tmp"
        predicate_cases = (
            ("repository-symlink", "is_symlink", repository_tmp),
            ("private-symlink", "is_symlink", workflow.PRIVATE_ROOT),
            ("private-directory", "is_dir", workflow.PRIVATE_ROOT),
        )

        def predicate_failure_hook(
            selected_method: str,
            selected_path: Path,
            failure: OSError | RuntimeError,
        ) -> Callable[[Path], bool]:
            def fail_selected_predicate(path: Path) -> bool:
                if path == selected_path:
                    raise failure
                if selected_method == "is_symlink":
                    return real_is_symlink(path)
                return real_is_dir(path)

            return fail_selected_predicate

        for label, method_name, raw_path in predicate_cases:
            for error_kind in ("os", "runtime"):
                with self.subTest(predicate=label, error=error_kind):
                    canary = f"LOCK-PREDICATE-{label}-{error_kind}-CANARY"
                    injected = lock_boundary_error(error_kind, canary, raw_path)

                    open_descriptor = mock.Mock()
                    flock = mock.Mock()
                    close = mock.Mock()
                    with (
                        mock.patch.object(
                            path_type,
                            method_name,
                            predicate_failure_hook(method_name, raw_path, injected),
                        ),
                        mock.patch.object(workflow.os, "open", open_descriptor),
                        mock.patch.object(workflow.fcntl, "flock", flock),
                        mock.patch.object(workflow.os, "close", close),
                    ):
                        raised, stdout, stderr = self.capture_operation_lock_failure()

                    self.assert_safe_lock_failure(
                        raised,
                        category="unsafe-lock-path",
                        canaries=(canary, str(raw_path)),
                        stdout=stdout,
                        stderr=stderr,
                    )
                    open_descriptor.assert_not_called()
                    flock.assert_not_called()
                    close.assert_not_called()

                    with workflow._operation_lock():
                        self.assertEqual(retained_private_files(), before)
                    self.assertEqual(retained_private_files(), before)

    def test_lock_descriptor_and_acquisition_failures_are_redacted(self) -> None:
        workflow._prepare_private_directories()
        before = retained_private_files()
        real_open = workflow.os.open
        real_close = workflow.os.close
        real_fstat = workflow.os.fstat
        real_stat = workflow.os.stat

        def metadata_stat_failure_hook(
            failure: OSError | RuntimeError,
            metadata_started: list[bool],
        ) -> Callable[..., os.stat_result]:
            def fail_metadata_stat(
                path: os.PathLike[str] | str | int,
                *args: object,
                **kwargs: object,
            ) -> os.stat_result:
                if (
                    metadata_started[0]
                    and path == workflow.PRIVATE_ROOT
                    and kwargs.get("follow_symlinks") is False
                ):
                    raise failure
                return real_stat(path, *args, **kwargs)  # type: ignore[arg-type]

            return fail_metadata_stat

        def metadata_fstat_hook(metadata_started: list[bool]) -> Callable[[int], os.stat_result]:
            def mark_metadata_started(descriptor: int) -> os.stat_result:
                metadata_started[0] = True
                return real_fstat(descriptor)

            return mark_metadata_started

        for error_kind in ("os", "runtime"):
            with self.subTest(operation="open", error=error_kind):
                canary = f"LOCK-OPEN-{error_kind}-CANARY"
                injected = lock_boundary_error(error_kind, canary, workflow.PRIVATE_ROOT)
                open_descriptor = mock.Mock(side_effect=injected)
                flock = mock.Mock()
                close = mock.Mock()
                with (
                    mock.patch.object(workflow.os, "open", open_descriptor),
                    mock.patch.object(workflow.fcntl, "flock", flock),
                    mock.patch.object(workflow.os, "close", close),
                ):
                    raised, stdout, stderr = self.capture_operation_lock_failure()

                self.assert_safe_lock_failure(
                    raised,
                    category="unsafe-lock-path",
                    canaries=(canary, str(workflow.PRIVATE_ROOT)),
                    stdout=stdout,
                    stderr=stderr,
                )
                open_descriptor.assert_called_once()
                flock.assert_not_called()
                close.assert_not_called()

                with workflow._operation_lock():
                    self.assertEqual(retained_private_files(), before)

            with self.subTest(operation="fstat", error=error_kind):
                canary = f"LOCK-FSTAT-{error_kind}-CANARY"
                injected = lock_boundary_error(error_kind, canary, workflow.PRIVATE_ROOT)
                with (
                    mock.patch.object(workflow.os, "open", wraps=real_open) as open_descriptor,
                    mock.patch.object(workflow.os, "fstat", side_effect=injected),
                    mock.patch.object(workflow.fcntl, "flock") as flock,
                    mock.patch.object(workflow.os, "close", wraps=real_close) as close,
                ):
                    raised, stdout, stderr = self.capture_operation_lock_failure()

                self.assert_safe_lock_failure(
                    raised,
                    category="unsafe-lock-path",
                    canaries=(canary, str(workflow.PRIVATE_ROOT)),
                    stdout=stdout,
                    stderr=stderr,
                )
                open_descriptor.assert_called_once()
                flock.assert_not_called()
                close.assert_called_once()

                with workflow._operation_lock():
                    self.assertEqual(retained_private_files(), before)

            with self.subTest(operation="stat", error=error_kind):
                canary = f"LOCK-STAT-{error_kind}-CANARY"
                injected = lock_boundary_error(error_kind, canary, workflow.PRIVATE_ROOT)
                metadata_started = [False]

                with (
                    mock.patch.object(workflow.os, "open", wraps=real_open) as open_descriptor,
                    mock.patch.object(
                        workflow.os,
                        "fstat",
                        side_effect=metadata_fstat_hook(metadata_started),
                    ),
                    mock.patch.object(
                        workflow.os,
                        "stat",
                        side_effect=metadata_stat_failure_hook(injected, metadata_started),
                    ),
                    mock.patch.object(workflow.fcntl, "flock") as flock,
                    mock.patch.object(workflow.os, "close", wraps=real_close) as close,
                ):
                    raised, stdout, stderr = self.capture_operation_lock_failure()

                self.assert_safe_lock_failure(
                    raised,
                    category="unsafe-lock-path",
                    canaries=(canary, str(workflow.PRIVATE_ROOT)),
                    stdout=stdout,
                    stderr=stderr,
                )
                open_descriptor.assert_called_once()
                flock.assert_not_called()
                close.assert_called_once()

                with workflow._operation_lock():
                    self.assertEqual(retained_private_files(), before)

            with self.subTest(operation="flock", error=error_kind):
                canary = f"LOCK-FLOCK-{error_kind}-CANARY"
                injected = lock_boundary_error(error_kind, canary, workflow.PRIVATE_ROOT)
                with (
                    mock.patch.object(workflow.os, "open", wraps=real_open) as open_descriptor,
                    mock.patch.object(workflow.fcntl, "flock", side_effect=injected) as flock,
                    mock.patch.object(workflow.os, "close", wraps=real_close) as close,
                ):
                    raised, stdout, stderr = self.capture_operation_lock_failure()

                self.assert_safe_lock_failure(
                    raised,
                    category="lock-acquire",
                    canaries=(canary, str(workflow.PRIVATE_ROOT)),
                    stdout=stdout,
                    stderr=stderr,
                )
                open_descriptor.assert_called_once()
                flock.assert_called_once()
                close.assert_called_once()

                with workflow._operation_lock():
                    self.assertEqual(retained_private_files(), before)

        self.assertEqual(retained_private_files(), before)

    def test_generated_boundary_categories_detach_ambient_context(self) -> None:
        workflow._prepare_private_directories()
        before = retained_private_files()
        path_metadata = os.stat(workflow.PRIVATE_ROOT, follow_symlinks=False)

        for ambient_kind in ("os", "runtime"):
            with self.subTest(category="mapped-resolution", ambient=ambient_kind):
                outer_path = self.case_dir / f"outer-resolution-{ambient_kind}"
                outer_canary = f"OUTER-RESOLUTION-{ambient_kind}-CANARY"
                ambient = lock_boundary_error(ambient_kind, outer_canary, outer_path)
                raw_canary = f"RAW-RESOLUTION-{ambient_kind}-CANARY"
                raw_error = PermissionError(
                    errno.EACCES,
                    raw_canary,
                    str(workflow.PRIVATE_ROOT),
                )
                open_descriptor = mock.Mock()
                flock = mock.Mock()
                close = mock.Mock()
                with (
                    mock.patch.object(workflow, "_resolved", side_effect=raw_error),
                    mock.patch.object(workflow.os, "open", open_descriptor),
                    mock.patch.object(workflow.fcntl, "flock", flock),
                    mock.patch.object(workflow.os, "close", close),
                ):
                    raised, stdout, stderr = self.capture_lock_failure_with_ambient(ambient)

                self.assert_safe_lock_failure(
                    raised,
                    category="unsafe-lock-path",
                    canaries=(
                        outer_canary,
                        str(outer_path),
                        raw_canary,
                        str(workflow.PRIVATE_ROOT),
                    ),
                    stdout=stdout,
                    stderr=stderr,
                )
                open_descriptor.assert_not_called()
                flock.assert_not_called()
                close.assert_not_called()

            with self.subTest(category="direct-validation", ambient=ambient_kind):
                outer_path = self.case_dir / f"outer-validation-{ambient_kind}"
                outer_canary = f"OUTER-VALIDATION-{ambient_kind}-CANARY"
                ambient = lock_boundary_error(ambient_kind, outer_canary, outer_path)
                open_descriptor = mock.Mock()
                flock = mock.Mock()
                close = mock.Mock()
                with (
                    mock.patch.object(workflow, "PRIVATE_ROOT", workflow.PROJECT_ROOT),
                    mock.patch.object(workflow.os, "open", open_descriptor),
                    mock.patch.object(workflow.fcntl, "flock", flock),
                    mock.patch.object(workflow.os, "close", close),
                ):
                    raised, stdout, stderr = self.capture_lock_failure_with_ambient(ambient)

                self.assert_safe_lock_failure(
                    raised,
                    category="unsafe-lock-path",
                    canaries=(outer_canary, str(outer_path)),
                    stdout=stdout,
                    stderr=stderr,
                )
                open_descriptor.assert_not_called()
                flock.assert_not_called()
                close.assert_not_called()

            acquisition_cases = (
                (
                    "lock-acquire",
                    OSError(
                        errno.EIO,
                        f"RAW-ACQUIRE-{ambient_kind}-CANARY",
                        str(workflow.PRIVATE_ROOT),
                    ),
                ),
                (
                    "concurrent-operation",
                    BlockingIOError(
                        errno.EWOULDBLOCK,
                        f"RAW-CONTENTION-{ambient_kind}-CANARY",
                        str(workflow.PRIVATE_ROOT),
                    ),
                ),
            )
            for expected_category, acquisition_error in acquisition_cases:
                with self.subTest(category=expected_category, ambient=ambient_kind):
                    outer_path = self.case_dir / f"outer-{expected_category}-{ambient_kind}"
                    outer_canary = f"OUTER-{expected_category}-{ambient_kind}-CANARY"
                    ambient = lock_boundary_error(ambient_kind, outer_canary, outer_path)
                    close = mock.Mock()
                    with (
                        mock.patch.object(workflow.os, "open", return_value=91),
                        mock.patch.object(
                            workflow.os,
                            "fstat",
                            return_value=path_metadata,
                        ),
                        mock.patch.object(
                            workflow.fcntl,
                            "flock",
                            side_effect=acquisition_error,
                        ) as flock,
                        mock.patch.object(workflow.os, "close", close),
                    ):
                        raised, stdout, stderr = self.capture_lock_failure_with_ambient(ambient)

                    self.assert_safe_lock_failure(
                        raised,
                        category=expected_category,
                        canaries=(
                            outer_canary,
                            str(outer_path),
                            str(acquisition_error),
                            str(workflow.PRIVATE_ROOT),
                        ),
                        stdout=stdout,
                        stderr=stderr,
                    )
                    flock.assert_called_once()
                    close.assert_called_once_with(91)

            with workflow._operation_lock():
                self.assertEqual(retained_private_files(), before)

        self.assertEqual(retained_private_files(), before)

    def test_lock_finalization_preserves_primary_failure_and_closes_once(self) -> None:
        workflow._prepare_private_directories()
        path_metadata = os.stat(workflow.PRIVATE_ROOT, follow_symlinks=False)
        for body_fails in (False, True):
            for unlock_fails in (False, True):
                for close_fails in (False, True):
                    label = f"body={body_fails},unlock={unlock_fails},close={close_fails}"
                    with self.subTest(case=label):
                        descriptor = 91

                        def flock(
                            _descriptor: int,
                            operation: int,
                            should_fail: bool = unlock_fails,
                        ) -> None:
                            if operation == workflow.fcntl.LOCK_UN and should_fail:
                                raise OSError("unlock-path-/private/lock-canary")

                        close = mock.Mock(
                            side_effect=(
                                OSError("close-path-/private/descriptor-canary")
                                if close_fails
                                else None
                            )
                        )
                        raised: BaseException | None = None
                        try:
                            with (
                                mock.patch.object(
                                    workflow.os,
                                    "open",
                                    return_value=descriptor,
                                ),
                                mock.patch.object(
                                    workflow.os,
                                    "fstat",
                                    return_value=path_metadata,
                                ),
                                mock.patch.object(
                                    workflow.fcntl,
                                    "flock",
                                    side_effect=flock,
                                ) as lock,
                                mock.patch.object(workflow.os, "close", close),
                            ):
                                with workflow._operation_lock():
                                    if body_fails:
                                        raise RuntimeError("body-failure")
                        except (ImportFailure, RuntimeError) as error:
                            raised = error

                        if body_fails:
                            self.assertIsInstance(raised, RuntimeError)
                            self.assertEqual(str(raised), "body-failure")
                        elif unlock_fails:
                            self.assertIsInstance(raised, ImportFailure)
                            import_failure = cast(ImportFailure, raised)
                            self.assertEqual(import_failure.category, "lock-release")
                        elif close_fails:
                            self.assertIsInstance(raised, ImportFailure)
                            import_failure = cast(ImportFailure, raised)
                            self.assertEqual(import_failure.category, "lock-close")
                        else:
                            self.assertIsNone(raised)
                        if isinstance(raised, ImportFailure):
                            rendered = "".join(traceback.format_exception(raised))
                            self.assertNotIn("/private/", rendered)
                            self.assertNotIn("canary", rendered)
                        self.assertEqual(lock.call_count, 2)
                        close.assert_called_once_with(descriptor)

    def test_lock_finalization_ignores_ambient_handled_exception_state(self) -> None:
        workflow._prepare_private_directories()
        path_metadata = os.stat(workflow.PRIVATE_ROOT, follow_symlinks=False)
        cases = (
            (False, False, None),
            (True, False, "lock-release"),
            (False, True, "lock-close"),
            (True, True, "lock-release"),
        )
        for ambient_kind in ("os", "runtime"):
            for unlock_fails, close_fails, expected_category in cases:
                label = f"unlock={unlock_fails},close={close_fails}"
                with self.subTest(case=label, ambient=ambient_kind):
                    descriptor = 91
                    outer_path = self.case_dir / f"outer-cleanup-{ambient_kind}"
                    outer_canary = f"OUTER-CLEANUP-{ambient_kind}-CANARY"
                    ambient = lock_boundary_error(ambient_kind, outer_canary, outer_path)
                    unlock_canary = f"RAW-UNLOCK-{ambient_kind}-CANARY"
                    close_canary = f"RAW-CLOSE-{ambient_kind}-CANARY"

                    def flock(
                        _descriptor: int,
                        operation: int,
                        should_fail: bool = unlock_fails,
                        failure_message: str = unlock_canary,
                    ) -> None:
                        if operation == workflow.fcntl.LOCK_UN and should_fail:
                            raise OSError(f"{failure_message}: /private/unlock")

                    close = mock.Mock(
                        side_effect=(
                            OSError(f"{close_canary}: /private/close") if close_fails else None
                        )
                    )
                    stdout = StringIO()
                    stderr = StringIO()
                    raised: ImportFailure | None = None
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        try:
                            raise ambient
                        except (OSError, RuntimeError):
                            try:
                                with (
                                    mock.patch.object(
                                        workflow.os,
                                        "open",
                                        return_value=descriptor,
                                    ),
                                    mock.patch.object(
                                        workflow.os,
                                        "fstat",
                                        return_value=path_metadata,
                                    ),
                                    mock.patch.object(
                                        workflow.fcntl,
                                        "flock",
                                        side_effect=flock,
                                    ) as lock,
                                    mock.patch.object(workflow.os, "close", close),
                                ):
                                    with workflow._operation_lock():
                                        pass
                            except ImportFailure as error:
                                raised = error

                    if expected_category is None:
                        self.assertIsNone(raised)
                    else:
                        self.assertIsNotNone(raised)
                        import_failure = cast(ImportFailure, raised)
                        self.assert_safe_lock_failure(
                            import_failure,
                            category=expected_category,
                            canaries=(
                                outer_canary,
                                str(outer_path),
                                unlock_canary,
                                close_canary,
                                "/private/",
                            ),
                            stdout=stdout.getvalue(),
                            stderr=stderr.getvalue(),
                        )
                    self.assertEqual(lock.call_count, 2)
                    close.assert_called_once_with(descriptor)

    def test_genuine_lock_failures_preserve_identity_context_and_precedence(self) -> None:
        workflow._prepare_private_directories()
        path_metadata = os.stat(workflow.PRIVATE_ROOT, follow_symlinks=False)
        before = retained_private_files()

        body_failures: tuple[BaseException, ...] = (
            RuntimeError("genuine-body-runtime"),
            ImportFailure("genuine-body-import-failure"),
        )
        for body_failure in body_failures:
            with self.subTest(stage="body", failure=type(body_failure).__name__):
                ambient = PermissionError(
                    errno.EACCES,
                    "GENUINE-BODY-OUTER-CANARY",
                    str(self.case_dir / "genuine-body-outer"),
                )

                def flock(_descriptor: int, operation: int) -> None:
                    if operation == workflow.fcntl.LOCK_UN:
                        raise OSError("simultaneous unlock cleanup failure")

                close = mock.Mock(side_effect=OSError("simultaneous close cleanup failure"))
                caught: BaseException | None = None
                try:
                    raise ambient
                except PermissionError:
                    try:
                        with (
                            mock.patch.object(workflow.os, "open", return_value=91),
                            mock.patch.object(
                                workflow.os,
                                "fstat",
                                return_value=path_metadata,
                            ),
                            mock.patch.object(
                                workflow.fcntl,
                                "flock",
                                side_effect=flock,
                            ) as lock,
                            mock.patch.object(workflow.os, "close", close),
                        ):
                            with workflow._operation_lock():
                                raise body_failure
                    except BaseException as error:
                        caught = error

                self.assertIs(caught, body_failure)
                caught_failure = cast(BaseException, caught)
                self.assertIs(caught_failure.__context__, ambient)
                self.assertEqual(lock.call_count, 2)
                close.assert_called_once_with(91)

        genuine_setup = ImportFailure("genuine-setup-import-failure")
        setup_ambient = PermissionError(
            errno.EACCES,
            "GENUINE-SETUP-OUTER-CANARY",
            str(self.case_dir / "genuine-setup-outer"),
        )
        open_descriptor = mock.Mock()
        setup_caught: BaseException | None = None
        try:
            raise setup_ambient
        except PermissionError:
            try:
                with (
                    mock.patch.object(workflow, "_resolved", side_effect=genuine_setup),
                    mock.patch.object(workflow.os, "open", open_descriptor),
                ):
                    with workflow._operation_lock():
                        self.fail("genuine setup failure unexpectedly yielded")
            except BaseException as error:
                setup_caught = error
        self.assertIs(setup_caught, genuine_setup)
        setup_failure = cast(BaseException, setup_caught)
        self.assertIs(setup_failure.__context__, setup_ambient)
        open_descriptor.assert_not_called()

        genuine_acquisition = ImportFailure("genuine-acquisition-import-failure")
        acquisition_ambient = PermissionError(
            errno.EACCES,
            "GENUINE-ACQUISITION-OUTER-CANARY",
            str(self.case_dir / "genuine-acquisition-outer"),
        )
        close = mock.Mock()
        acquisition_caught: BaseException | None = None
        try:
            raise acquisition_ambient
        except PermissionError:
            try:
                with (
                    mock.patch.object(workflow.os, "open", return_value=91),
                    mock.patch.object(
                        workflow.os,
                        "fstat",
                        return_value=path_metadata,
                    ),
                    mock.patch.object(
                        workflow.fcntl,
                        "flock",
                        side_effect=genuine_acquisition,
                    ),
                    mock.patch.object(workflow.os, "close", close),
                ):
                    with workflow._operation_lock():
                        self.fail("genuine acquisition failure unexpectedly yielded")
            except BaseException as error:
                acquisition_caught = error
        self.assertIs(acquisition_caught, genuine_acquisition)
        acquisition_failure = cast(BaseException, acquisition_caught)
        self.assertIs(acquisition_failure.__context__, acquisition_ambient)
        close.assert_called_once_with(91)

        with workflow._operation_lock():
            self.assertEqual(retained_private_files(), before)
        self.assertEqual(retained_private_files(), before)

    def test_lock_acquisition_failure_remains_primary_during_close_failure(self) -> None:
        workflow._prepare_private_directories()
        descriptor = 91
        close = mock.Mock(side_effect=OSError("close-path-/private/descriptor-canary"))
        with (
            mock.patch.object(workflow.os, "open", return_value=descriptor),
            mock.patch.object(
                workflow.os,
                "fstat",
                side_effect=OSError("stat-path-/private/root-canary"),
            ),
            mock.patch.object(workflow.os, "close", close),
            self.assertRaises(ImportFailure) as raised,
        ):
            with workflow._operation_lock():
                self.fail("metadata failure unexpectedly yielded")

        self.assertEqual(raised.exception.category, "unsafe-lock-path")
        rendered = "".join(traceback.format_exception(raised.exception))
        self.assertNotIn("/private/", rendered)
        self.assertNotIn("canary", rendered)
        close.assert_called_once_with(descriptor)

    def test_nested_lock_context_fails_closed_then_allows_retry(self) -> None:
        workflow._prepare_private_directories()
        before = retained_private_files()
        with workflow._operation_lock():
            with self.assertRaises(ImportFailure) as nested:
                with workflow._operation_lock():
                    self.fail("nested lock unexpectedly acquired")
            self.assertEqual(nested.exception.category, "concurrent-operation")
            self.assertEqual(retained_private_files(), before)

        with workflow._operation_lock():
            self.assertEqual(retained_private_files(), before)
        self.assertEqual(retained_private_files(), before)

    def test_unlock_failure_closes_real_descriptor_and_fresh_process_retries(self) -> None:
        snapshot_id = f"{self.snapshot_id}-unlock"
        artifact = workflow.ARTIFACTS_DIR / f"{snapshot_id}.sqlite3"
        report = workflow.REPORTS_DIR / f"{snapshot_id}.json"
        workflow._prepare_private_directories()
        artifact.write_bytes(b"owned-artifact")
        report.write_bytes(b"owned-report")
        source_before = fingerprint(self.source)
        real_flock = workflow.fcntl.flock

        def fail_explicit_unlock(descriptor: int, operation: int) -> None:
            if operation == workflow.fcntl.LOCK_UN:
                raise OSError("synthetic unlock failure")
            real_flock(descriptor, operation)

        with (
            mock.patch.object(
                workflow.fcntl,
                "flock",
                side_effect=fail_explicit_unlock,
            ),
            self.assertRaises(ImportFailure) as release_failure,
        ):
            with workflow._operation_lock():
                pass
        self.assertEqual(release_failure.exception.category, "lock-release")
        self.assertEqual(fingerprint(self.source), source_before)
        self.assertEqual(
            retained_private_files(),
            [
                f"artifacts/{snapshot_id}.sqlite3",
                f"reports/{snapshot_id}.json",
            ],
        )

        retry_code = """
import sys
from pathlib import Path

import review_import.workflow as workflow

root = Path(sys.argv[1])
workflow.PRIVATE_ROOT = root
workflow.ARTIFACTS_DIR = root / 'artifacts'
workflow.REPORTS_DIR = root / 'reports'
workflow.WORK_DIR = root / 'work'
with workflow._operation_lock():
    pass
"""
        result = subprocess.run(
            [sys.executable, "-c", retry_code, str(workflow.PRIVATE_ROOT)],
            cwd=PROJECT_ROOT,
            env={**os.environ, "DTC_ENVIRONMENT": "test"},
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            retained_private_files(),
            [
                f"artifacts/{snapshot_id}.sqlite3",
                f"reports/{snapshot_id}.json",
            ],
        )
        self.assertEqual(
            cleanup_snapshot(snapshot_id),
            {"sanitized": 1, "report": 1, "target": 0},
        )
        self.assertEqual(retained_private_files(), [])

    def test_deployed_environment_and_unsafe_paths_are_refused(self) -> None:
        os.environ["DTC_ENVIRONMENT"] = "development"
        with self.assertRaises(ImportFailure) as deployed:
            ReviewImporter().run(self.config())
        self.assertEqual(deployed.exception.category, "deployed-environment-refused")
        with self.assertRaises(ImportFailure) as deployed_cleanup:
            cleanup_snapshot(self.snapshot_id)
        self.assertEqual(deployed_cleanup.exception.category, "deployed-environment-refused")

        os.environ["DTC_ENVIRONMENT"] = "test"
        with self.assertRaises(ImportFailure) as target_error:
            ReviewImporter().run(self.config(target_db=PROJECT_ROOT / "README.md"))
        self.assertEqual(target_error.exception.category, "unsafe-target-path")

    def test_legacy_broad_loader_is_disabled_before_any_path_access(self) -> None:
        stderr = StringIO()
        with (
            mock.patch.object(load_rds_export, "parse_args") as parse_args,
            mock.patch.object(load_rds_export, "resolve_import_paths") as resolve_paths,
            mock.patch.object(load_rds_export, "rebuild_database") as rebuild,
            mock.patch.object(load_rds_export, "replace_rebuilt_database") as replace,
            redirect_stderr(stderr),
        ):
            result = load_rds_export.main()

        self.assertEqual(result, 2)
        parse_args.assert_not_called()
        resolve_paths.assert_not_called()
        rebuild.assert_not_called()
        replace.assert_not_called()
        output = stderr.getvalue()
        self.assertIn("broad RDS loader is disabled", output)
        self.assertIn("scripts/build_local_review_db.py", output)
