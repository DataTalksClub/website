from __future__ import annotations

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class IdentityMigrationWindowTests(TransactionTestCase):
    serialized_rollback = True

    migrate_from = ("accounts", "0010_remove_customuser_email_course_updates_and_more")
    migrate_to = ("accounts", "0012_backfill_normalized_identity")
    allauth_target = ("account", "0009_emailaddress_unique_primary_email")

    def setUp(self) -> None:
        super().setUp()
        self.executor = MigrationExecutor(connection)
        old_targets = [self.migrate_from, self.allauth_target]
        self.executor.migrate(old_targets)
        old_apps = self.executor.loader.project_state(old_targets).apps
        User = old_apps.get_model("accounts", "CustomUser")
        EmailAddress = old_apps.get_model("account", "EmailAddress")

        self.clean = User.objects.create(
            username="clean",
            email="Clean@Example.Invalid",
            password="synthetic-hash",
        )
        EmailAddress.objects.create(
            user_id=self.clean.pk,
            email="Clean@Example.Invalid",
            verified=True,
            primary=True,
        )
        self.first_duplicate = User.objects.create(
            username="duplicate-one",
            email="Duplicate@Example.Invalid",
            password="synthetic-hash-one",
        )
        self.second_duplicate = User.objects.create(
            username="duplicate-two",
            email="duplicate@example.invalid",
            password="synthetic-hash-two",
        )
        EmailAddress.objects.create(
            user_id=self.first_duplicate.pk,
            email="Duplicate@Example.Invalid",
            verified=True,
            primary=True,
        )
        EmailAddress.objects.create(
            user_id=self.second_duplicate.pk,
            email="duplicate@example.invalid",
            verified=True,
            primary=True,
        )
        self.legacy = User.objects.create(
            username="legacy-no-email",
            email="",
            password="synthetic-hash-three",
        )

    def tearDown(self) -> None:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_forward_backward_forward_preserves_ids_and_classifies_conflicts(self) -> None:
        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_to])
        new_apps = self.executor.loader.project_state([self.migrate_to]).apps
        User = new_apps.get_model("accounts", "CustomUser")

        clean = User.objects.get(pk=self.clean.pk)
        first = User.objects.get(pk=self.first_duplicate.pk)
        second = User.objects.get(pk=self.second_duplicate.pk)
        legacy = User.objects.get(pk=self.legacy.pk)
        self.assertEqual(clean.normalized_email, "clean@example.invalid")
        self.assertEqual(clean.identity_state, "active")
        self.assertEqual(first.identity_state, "quarantined")
        self.assertEqual(second.identity_state, "quarantined")
        self.assertIsNone(legacy.normalized_email)
        self.assertEqual(legacy.identity_state, "legacy")
        self.assertEqual(clean.password, "synthetic-hash")

        self.executor = MigrationExecutor(connection)
        backward_targets = [self.migrate_from, self.allauth_target]
        self.executor.migrate(backward_targets)
        backward_apps = self.executor.loader.project_state(backward_targets).apps
        BackwardUser = backward_apps.get_model("accounts", "CustomUser")
        self.assertEqual(
            list(
                BackwardUser.objects.order_by("pk").values_list("pk", flat=True)
            ),
            [
                self.clean.pk,
                self.first_duplicate.pk,
                self.second_duplicate.pk,
                self.legacy.pk,
            ],
        )

        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_to])
        forward_again_apps = self.executor.loader.project_state(
            [self.migrate_to]
        ).apps
        ForwardAgainUser = forward_again_apps.get_model(
            "accounts",
            "CustomUser",
        )
        self.assertEqual(
            ForwardAgainUser.objects.get(pk=self.clean.pk).identity_state,
            "active",
        )
