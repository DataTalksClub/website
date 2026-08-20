from __future__ import annotations

import hashlib
import unittest
from datetime import UTC, datetime
from pathlib import Path

from django.conf import settings
from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase

from test_support.migrations import (
    assert_data_migration_isolation,
    assert_stable_migration_module_isolation,
    data_migration_functions,
    load_migration_seed,
)

ROOT = Path(__file__).resolve().parents[2]
SEED_ROOT = ROOT / "test_support" / "migration_seeds"
FROZEN_AT = datetime(2026, 2, 3, 4, 5, 6, tzinfo=UTC)


class MigrationSeedContractTests(SimpleTestCase):
    def test_every_versioned_seed_has_exact_checksum_nodes_counts_and_unresolved_cases(
        self,
    ) -> None:
        seeds = tuple(load_migration_seed(path) for path in sorted(SEED_ROOT.glob("*.json")))
        self.assertEqual(
            {seed.seed_version for seed in seeds},
            {
                "accounts-identity-v1",
                "accounts-profile-v1",
                "content-active-paths-v1",
            },
        )
        for seed in seeds:
            with self.subTest(seed=seed.seed_version):
                self.assertGreater(seed.expected["rows"], 0)
                self.assertEqual(seed.expected["unresolved"], [])
                self.assertNotEqual(seed.start, seed.target)
        self.assertEqual(
            {seed.seed_version: seed.reversible for seed in seeds},
            {
                "accounts-identity-v1": True,
                "accounts-profile-v1": True,
                "content-active-paths-v1": True,
            },
        )

    def test_every_data_migration_uses_historical_apps_without_runtime_side_effects(self) -> None:
        paths = tuple(
            path
            for path in sorted(ROOT.glob("*/migrations/[0-9]*.py"))
            if data_migration_functions(path)
        )
        self.assertEqual(
            {path.relative_to(ROOT).as_posix() for path in paths},
            {
                "accounts/migrations/0005_backfill_certificate_name_from_enrollment.py",
                "accounts/migrations/0012_backfill_normalized_identity.py",
                "content/migrations/0002_active_content_path_claims.py",
                "courses/migrations/0002_curriculum_and_project_criteria.py",
                "courses/migrations/0005_cohort_identifier_and_more.py",
                "events/migrations/0005_seed_event_identity_manifest.py",
                "events/migrations/0006_event_public_id.py",
                "events/migrations/0007_reconcile_public_event_identity.py",
                "events/migrations/0008_align_public_event_ids_to_manifest.py",
            },
        )
        for path in paths:
            with self.subTest(path=path):
                assert_data_migration_isolation(path)
                source = path.read_text(encoding="utf-8")
                self.assertIn("apps.get_model", source)

    def test_historical_content_migrations_do_not_transitively_import_current_apps(self) -> None:
        migration_module = ROOT / "content" / "migration_validators.py"
        migration_paths = (
            ROOT / "content" / "migrations" / "0001_initial.py",
            ROOT / "content" / "migrations" / "0002_active_content_path_claims.py",
        )
        for path in migration_paths:
            self.assertIn(
                "import content.migration_validators",
                path.read_text(encoding="utf-8"),
            )
        assert_stable_migration_module_isolation(migration_module)

    def test_stable_migration_module_boundary_rejects_transitive_current_app_import(
        self,
    ) -> None:
        layout = settings.TEST_RUNTIME.worker("migration-import-boundary")
        unsafe_module = layout.artifacts / "unsafe_migration_validator.py"
        unsafe_module.parent.mkdir(parents=True, exist_ok=True)
        unsafe_module.write_text(
            "from core.redaction import is_sensitive_text\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            ValueError,
            "imports mutable runtime code",
        ):
            assert_stable_migration_module_isolation(unsafe_module)


class IsolatedMigrationExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        digest = hashlib.sha256(self.id().encode("utf-8")).hexdigest()[:12]
        self.worker_id = f"migration-{digest}"
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

    def test_clean_database_migrates_from_zero_to_every_leaf(self) -> None:
        executor = MigrationExecutor(self.connection)
        leaves = executor.loader.graph.leaf_nodes()
        self.assertEqual(executor.loader.applied_migrations, {})
        executor.migrate(leaves)
        applied = MigrationExecutor(self.connection).loader.applied_migrations
        self.assertTrue(all(leaf in applied for leaf in leaves))

    def test_course_phase_two_schema_has_reusable_families_and_cohort_backed_relations(self) -> None:
        courses_target = ("courses", "0001_initial")
        _executor, apps = self._migrate([courses_target])
        Course = apps.get_model("courses", "Course")
        Cohort = apps.get_model("courses", "Cohort")

        self.assertEqual(Course._meta.db_table, "courses_course_family")
        self.assertEqual(Cohort._meta.db_table, "courses_course")
        self.assertEqual(
            Cohort._meta.get_field("course").remote_field.model._meta.model_name,
            "course",
        )
        self.assertEqual(Cohort._meta.get_field("year").default, 2026)
        self.assertTrue(Cohort._meta.get_field("uuid").unique)
        self.assertTrue(Cohort._meta.get_field("outcome").blank)
        self.assertEqual(
            apps.get_model("courses", "Enrollment")
            ._meta.get_field("course")
            .remote_field.model._meta.model_name,
            "cohort",
        )
        self.assertEqual(
            apps.get_model("courses", "Homework")
            ._meta.get_field("course")
            .remote_field.model._meta.model_name,
            "cohort",
        )

    def test_accounts_profile_seed_upgrades_with_historical_models_and_noop_reverse(self) -> None:
        seed = load_migration_seed(SEED_ROOT / "accounts-profile-v1.json")
        self.assertTrue(seed.reversible)
        courses_target = ("courses", "0001_initial")
        executor, apps = self._migrate([seed.start, courses_target])
        User = apps.get_model("accounts", "CustomUser")
        Course = apps.get_model("courses", "Course")
        Cohort = apps.get_model("courses", "Cohort")
        Enrollment = apps.get_model("courses", "Enrollment")
        user_values = seed.payload["users"][0]
        user = User.objects.create(
            id=user_values["id"],
            username=user_values["username"],
            email="synthetic-profile@example.invalid",
            password="synthetic-hash",
        )
        family = Course.objects.create(
            slug="synthetic-profile-family",
            title="Synthetic profile family",
        )
        course = Cohort.objects.create(
            course=family,
            slug="synthetic-profile-course",
            title="Synthetic profile course",
            description="Synthetic",
        )
        enrollment_values = seed.payload["enrollments"][0]
        Enrollment.objects.create(
            id=enrollment_values["id"],
            student_id=user.pk,
            course_id=course.pk,
            display_name="Synthetic learner",
            certificate_name=enrollment_values["certificate_name"],
        )

        executor, apps = self._migrate([seed.target])
        MigratedUser = apps.get_model("accounts", "CustomUser")
        migrated = MigratedUser.objects.get(pk=user.pk)
        self.assertEqual(migrated.certificate_name, "Synthetic Learner")

        executor, apps = self._migrate([seed.start, courses_target])
        ReversedUser = apps.get_model("accounts", "CustomUser")
        self.assertEqual(ReversedUser.objects.get(pk=user.pk).username, "synthetic-profile")
        executor, apps = self._migrate([seed.target, courses_target])
        ResumedUser = apps.get_model("accounts", "CustomUser")
        self.assertEqual(
            ResumedUser.objects.get(pk=user.pk).certificate_name,
            "Synthetic Learner",
        )

    def test_accounts_identity_seed_is_idempotent_and_reversible(self) -> None:
        seed = load_migration_seed(SEED_ROOT / "accounts-identity-v1.json")
        self.assertTrue(seed.reversible)
        account_email_target = ("account", "0009_emailaddress_unique_primary_email")
        executor, apps = self._migrate([seed.start, account_email_target])
        User = apps.get_model("accounts", "CustomUser")
        EmailAddress = apps.get_model("account", "EmailAddress")
        for values in seed.payload["users"]:
            User.objects.create(password="synthetic-hash", **values)
        for values in seed.payload["verified_emails"]:
            EmailAddress.objects.create(verified=True, primary=True, **values)

        executor, apps = self._migrate([seed.target, account_email_target])
        MigratedUser = apps.get_model("accounts", "CustomUser")
        counts = {
            state: MigratedUser.objects.filter(identity_state=state).count()
            for state in ("active", "legacy", "quarantined")
        }
        self.assertEqual(
            counts,
            {
                "active": seed.expected["active"],
                "legacy": seed.expected["legacy"],
                "quarantined": seed.expected["quarantined"],
            },
        )
        executor, apps = self._migrate([seed.start, account_email_target])
        self.assertEqual(apps.get_model("accounts", "CustomUser").objects.count(), 4)
        executor, apps = self._migrate([seed.target, account_email_target])
        self.assertEqual(
            apps.get_model("accounts", "CustomUser").objects.get(pk=101).normalized_email,
            "clean@example.invalid",
        )

    def test_content_path_seed_converges_and_noop_reverse_preserves_source_rows(self) -> None:
        seed = load_migration_seed(SEED_ROOT / "content-active-paths-v1.json")
        self.assertTrue(seed.reversible)
        executor, apps = self._migrate([seed.start])
        ContentSource = apps.get_model("content", "ContentSource")
        ContentRelease = apps.get_model("content", "ContentRelease")
        ContentDocument = apps.get_model("content", "ContentDocument")
        ContentAsset = apps.get_model("content", "ContentAsset")
        source = ContentSource.objects.create(
            stable_id=seed.payload["source"]["stable_id"],
            display_name="Synthetic migration",
            repository_owner="DataTalksClub",
            repository_name="synthetic-migration",
            branch="main",
            path_allowlist=["content/"],
            adapter_type="fixture",
            mount_path="/",
            enabled=True,
            last_successful_commit="a" * 40,
        )
        release = ContentRelease.objects.create(
            source=source,
            sequence=1,
            commit_sha="a" * 40,
            parser_version="synthetic-v1",
            rendering_version="synthetic-v1",
            status="active",
            requested_at=FROZEN_AT,
            fetched_at=FROZEN_AT,
            validated_at=FROZEN_AT,
            activated_at=FROZEN_AT,
            document_count=1,
            asset_count=1,
            asset_manifest_checksum="b" * 64,
        )
        ContentDocument.objects.create(
            release=release,
            content_kind="fixture",
            stable_key="synthetic-migration",
            source_path="synthetic.md",
            checksum="c" * 64,
            exact_public_path=seed.payload["documents"][0]["path"],
            title="Synthetic migration",
            is_published=True,
        )
        ContentAsset.objects.create(
            release=release,
            source_path="synthetic.svg",
            stable_public_path=seed.payload["assets"][0]["path"],
            storage_key=f"content/{source.stable_id}/{release.id}/synthetic.svg",
            content_type="image/svg+xml",
            size=1,
            checksum="d" * 64,
        )
        ContentSource.objects.filter(pk=source.pk).update(active_release_id=release.pk)

        executor, apps = self._migrate([seed.target])
        ActivePath = apps.get_model("content", "ActiveContentPath")
        self.assertEqual(
            set(ActivePath.objects.values_list("exact_public_path", flat=True)),
            {"/Synthetic/Migration.html", "/assets/synthetic-migration.svg"},
        )
        executor, apps = self._migrate([seed.start])
        self.assertEqual(apps.get_model("content", "ContentSource").objects.count(), 1)
        executor, apps = self._migrate([seed.target])
        self.assertEqual(apps.get_model("content", "ActiveContentPath").objects.count(), 2)
        executor, apps = self._migrate([seed.target])
        self.assertEqual(apps.get_model("content", "ActiveContentPath").objects.count(), 2)

    def _migrate(self, targets: list[tuple[str, str]]):
        executor = MigrationExecutor(self.connection)
        executor.migrate(targets)
        apps = executor.loader.project_state(targets).apps
        return executor, apps
