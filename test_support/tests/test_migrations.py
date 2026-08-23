from __future__ import annotations

import hashlib
import unittest
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from django.conf import settings
from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase

from test_support.factories.context import canonical_json_bytes
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
                "content-contract-digest-v1",
                "courses-legacy-history-v1",
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
                "content-contract-digest-v1": False,
                "courses-legacy-history-v1": False,
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
                "courses/migrations/0003_replace_commas_with_linebreaks_in_possible_answers.py",
                "courses/migrations/0004_update_correct_answer_indexes.py",
                "courses/migrations/0005_update_answers_with_indexes.py",
                "courses/migrations/0006_course_first_homework_scored.py",
                "courses/migrations/0042_course_schema_bridge.py",
                "courses/migrations/0043_curriculum_and_project_criteria.py",
                "courses/migrations/0046_cohort_identifier_and_more.py",
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
        self.assertIn(
            ("content", "0006_finalize_content_release_contract_digest"),
            leaves,
        )
        executor.migrate(leaves)
        applied = MigrationExecutor(self.connection).loader.applied_migrations
        self.assertTrue(all(leaf in applied for leaf in leaves))

    def test_content_contract_digest_populated_pre_repair_path_preserves_rows_and_is_idempotent(
        self,
    ) -> None:
        seed = load_migration_seed(SEED_ROOT / "content-contract-digest-v1.json")
        target = seed.target
        executor, apps = self._migrate([seed.start])
        self._populate_content_contract_fixture(apps, seed)
        before = self._content_contract_snapshot(apps)

        executor = MigrationExecutor(self.connection)
        executor.migrate([target])
        migrated_apps = MigrationExecutor(self.connection).loader.project_state([target]).apps
        after = self._content_contract_snapshot(migrated_apps)
        self.assertEqual(after, before)
        self.assertEqual(
            after["aggregate"],
            {
                "counts": {
                    "active_paths": seed.expected["active_paths"],
                    "assets": seed.expected["assets"],
                    "documents": seed.expected["documents"],
                    "relations": seed.expected["relations"],
                    "releases": seed.expected["releases"],
                },
                "checksum": before["aggregate"]["checksum"],
            },
        )
        self.assertEqual(
            set(
                migrated_apps.get_model("content", "ContentRelease").objects.values_list(
                    "public_contracts_sha256", flat=True
                )
            ),
            {seed.payload["releases"][0]["digest"]},
        )
        self._assert_one_contract_constraint()

        executor = MigrationExecutor(self.connection)
        executor.migrate([target])
        reapplied = self._content_contract_snapshot(
            MigrationExecutor(self.connection).loader.project_state([target]).apps
        )
        self.assertEqual(reapplied, after)

        applied = MigrationExecutor(self.connection).loader.applied_migrations
        self.assertIn(("content", "0005_repair_content_release_contract_digest"), applied)
        self.assertIn(("content", "0006_finalize_content_release_contract_digest"), applied)
        self.assertIn(
            ("content", "0004_remove_contentrelease_content_release_contract_sha_ck_and_more"),
            applied,
        )

    def test_content_contract_digest_already_recorded_0004_state_converges_without_data_loss(
        self,
    ) -> None:
        seed = load_migration_seed(SEED_ROOT / "content-contract-digest-v1.json")
        published_0004 = (
            "content",
            "0004_remove_contentrelease_content_release_contract_sha_ck_and_more",
        )
        original_executor = MigrationExecutor(self.connection)
        original_executor.loader = MigrationLoader(self.connection, replace_migrations=False)
        original_executor.migrate([published_0004])
        original_apps = original_executor.loader.project_state([published_0004]).apps
        self._populate_content_contract_fixture(
            original_apps,
            seed,
            digest="31f505350566bfcde0a30109dadcfb3565042fd395b4c1bd151966f94d361332",
        )
        before = self._content_contract_snapshot(original_apps)

        target = seed.target
        executor = MigrationExecutor(self.connection)
        executor.migrate([target])
        migrated_apps = MigrationExecutor(self.connection).loader.project_state([target]).apps
        self.assertEqual(self._content_contract_snapshot(migrated_apps), before)
        self._assert_one_contract_constraint()

        ContentRelease = migrated_apps.get_model("content", "ContentRelease")
        source_id = UUID(seed.payload["source"]["id"])
        legacy = ContentRelease.objects.create(
            id=UUID("00000000-0000-0000-0000-000000002203"),
            source_id=source_id,
            sequence=3,
            commit_sha="c" * 40,
            parser_version="contract-digest-v1",
            rendering_version="contract-digest-v1",
            status="queued",
            requested_at=FROZEN_AT,
            public_contracts_sha256=seed.payload["releases"][0]["digest"],
        )
        self.assertEqual(
            ContentRelease.objects.get(pk=legacy.pk).public_contracts_sha256,
            seed.payload["releases"][0]["digest"],
        )
        applied = MigrationExecutor(self.connection).loader.applied_migrations
        self.assertIn(published_0004, applied)
        self.assertIn(("content", "0006_finalize_content_release_contract_digest"), applied)

    def _populate_content_contract_fixture(self, apps, seed, *, digest: str | None = None) -> None:
        payload = seed.payload
        ContentSource = apps.get_model("content", "ContentSource")
        ContentRelease = apps.get_model("content", "ContentRelease")
        ContentDocument = apps.get_model("content", "ContentDocument")
        ContentRelation = apps.get_model("content", "ContentRelation")
        ContentAsset = apps.get_model("content", "ContentAsset")
        ActiveContentPath = apps.get_model("content", "ActiveContentPath")
        source = ContentSource.objects.create(
            id=UUID(payload["source"]["id"]),
            stable_id=payload["source"]["stable_id"],
            display_name="Synthetic contract digest source",
            repository_owner="DataTalksClub",
            repository_name="contract-digest-fixture",
            branch="main",
            path_allowlist=["content/"],
            adapter_type="fixture",
            mount_path="/",
            enabled=payload["source"]["enabled"],
            last_successful_commit=payload["source"]["last_successful_commit"],
        )
        for release_payload in payload["releases"]:
            ContentRelease.objects.create(
                id=UUID(release_payload["id"]),
                source=source,
                sequence=release_payload["sequence"],
                based_on_release_id=release_payload["based_on_release_id"],
                commit_sha=release_payload["commit_sha"],
                parser_version="contract-digest-fixture-v1",
                rendering_version="contract-digest-fixture-v1",
                status=release_payload["status"],
                requested_at=FROZEN_AT,
                fetched_at=FROZEN_AT,
                validated_at=FROZEN_AT,
                activated_at=FROZEN_AT,
                superseded_at=(FROZEN_AT if release_payload["status"] == "superseded" else None),
                document_count=1,
                relation_count=1,
                asset_count=1,
                asset_manifest_checksum=release_payload["asset_manifest_checksum"],
                public_contracts_sha256=digest or release_payload["digest"],
                request_provenance={"fixture": "content-contract-digest-v1"},
            )
        for document_payload in payload["documents"]:
            ContentDocument.objects.create(
                id=UUID(document_payload["id"]),
                release_id=document_payload["release_id"],
                content_kind="fixture",
                stable_key=document_payload["stable_key"],
                source_path=f"{document_payload['stable_key']}.md",
                checksum=document_payload["checksum"],
                exact_public_path=document_payload["path"],
                title=document_payload["stable_key"],
                raw_frontmatter={"fixture": "content-contract-digest-v1"},
                raw_body=f"# {document_payload['stable_key']}",
                raw_structured_data="{}",
                rendered_html=f"<h1>{document_payload['stable_key']}</h1>",
                normalized_text=document_payload["stable_key"],
                is_published=True,
            )
        for relation_payload in payload["relations"]:
            ContentRelation.objects.create(
                id=UUID(relation_payload["id"]),
                source_document_id=relation_payload["source_document_id"],
                relation_type=relation_payload["type"],
                target_kind=relation_payload["target_kind"],
                target_key=relation_payload["target_key"],
                resolved_target_document_id=relation_payload.get("resolved_target_document_id"),
                resolved_public_path=relation_payload.get("resolved_public_path"),
                order=relation_payload["order"],
                is_required=True,
            )
        for asset_payload in payload["assets"]:
            ContentAsset.objects.create(
                id=UUID(asset_payload["id"]),
                release_id=asset_payload["release_id"],
                source_path=asset_payload["path"].rsplit("/", 1)[-1],
                stable_public_path=asset_payload["path"],
                storage_key=asset_payload["storage_key"],
                content_type="image/svg+xml",
                size=128,
                checksum=asset_payload["checksum"],
            )
        ContentSource.objects.filter(pk=source.pk).update(
            active_release_id=payload["source"]["active_release_id"]
        )
        for path_payload in payload["active_paths"]:
            ActiveContentPath.objects.create(
                path_digest=path_payload["path_digest"],
                exact_public_path=path_payload["path"],
                source_id=path_payload["source_id"],
                release_id=path_payload["release_id"],
            )

    def _content_contract_snapshot(self, apps) -> dict:
        models = {
            "source": apps.get_model("content", "ContentSource"),
            "releases": apps.get_model("content", "ContentRelease"),
            "documents": apps.get_model("content", "ContentDocument"),
            "relations": apps.get_model("content", "ContentRelation"),
            "assets": apps.get_model("content", "ContentAsset"),
            "active_paths": apps.get_model("content", "ActiveContentPath"),
        }
        fields = {
            "source": (
                "id",
                "active_release_id",
                "stable_id",
                "enabled",
                "last_successful_commit",
                "revision",
            ),
            "releases": (
                "id",
                "source_id",
                "sequence",
                "based_on_release_id",
                "commit_sha",
                "status",
                "asset_manifest_checksum",
                "public_contracts_sha256",
                "document_count",
                "relation_count",
                "asset_count",
                "request_provenance",
            ),
            "documents": (
                "id",
                "release_id",
                "stable_key",
                "exact_public_path",
                "checksum",
                "raw_body",
                "raw_structured_data",
                "rendered_html",
                "adapter_metadata",
            ),
            "relations": (
                "id",
                "source_document_id",
                "relation_type",
                "target_kind",
                "target_key",
                "resolved_target_document_id",
                "resolved_public_path",
                "order",
            ),
            "assets": (
                "id",
                "release_id",
                "source_path",
                "stable_public_path",
                "storage_key",
                "size",
                "checksum",
            ),
            "active_paths": (
                "path_digest",
                "exact_public_path",
                "source_id",
                "release_id",
            ),
        }
        rows = {
            name: list(model.objects.order_by("pk").values(*fields[name]))
            for name, model in models.items()
        }
        counts = {name: len(values) for name, values in rows.items() if name != "source"}
        checksum = hashlib.sha256(canonical_json_bytes(rows)).hexdigest()
        return {"rows": rows, "aggregate": {"counts": counts, "checksum": checksum}}

    def _assert_one_contract_constraint(self) -> None:
        with self.connection.cursor() as cursor:
            constraints = self.connection.introspection.get_constraints(
                cursor,
                "content_contentrelease",
            )
        self.assertEqual(
            sum(name == "content_release_contract_sha_ck" for name in constraints),
            1,
        )

    def test_course_phase_two_schema_has_reusable_families_and_cohort_backed_relations(
        self,
    ) -> None:
        courses_target = ("courses", "0042_course_schema_bridge")
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
        executor, apps = self._migrate(
            [seed.start, courses_target],
            replace_migrations=False,
        )
        User = apps.get_model("accounts", "CustomUser")
        Course = apps.get_model("courses", "Course")
        Enrollment = apps.get_model("courses", "Enrollment")
        user_values = seed.payload["users"][0]
        user = User.objects.create(
            id=user_values["id"],
            username=user_values["username"],
            email="synthetic-profile@example.invalid",
            password="synthetic-hash",
        )
        course = Course.objects.create(
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

        executor, apps = self._migrate([seed.target], replace_migrations=False)
        MigratedUser = apps.get_model("accounts", "CustomUser")
        migrated = MigratedUser.objects.get(pk=user.pk)
        self.assertEqual(migrated.certificate_name, "Synthetic Learner")

        executor, apps = self._migrate(
            [seed.start, courses_target],
            replace_migrations=False,
        )
        ReversedUser = apps.get_model("accounts", "CustomUser")
        self.assertEqual(ReversedUser.objects.get(pk=user.pk).username, "synthetic-profile")
        executor, apps = self._migrate(
            [seed.target, courses_target],
            replace_migrations=False,
        )
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

    def _migrate(
        self,
        targets: list[tuple[str, str]],
        *,
        replace_migrations: bool = True,
    ):
        executor = MigrationExecutor(self.connection)
        executor.loader = MigrationLoader(
            self.connection,
            replace_migrations=replace_migrations,
        )
        executor.migrate(targets)
        apps = executor.loader.project_state(targets).apps
        return executor, apps
