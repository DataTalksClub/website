from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import DatabaseError, IntegrityError, close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature

from core.audit import AuditWriteContext, record_audit_event
from core.configuration import (
    InvalidOperationalSetting,
    OperationalSettingDefinition,
    register_operational_setting,
    resolve_operational_setting,
    set_operational_setting,
)
from core.context import AuditContext as ExecutionAuditContext
from core.context import context_scope, current_context
from core.idempotency import (
    IdempotencyConflict,
    IdempotencyInProgress,
    JsonObject,
    UnsafeJsonValue,
    canonical_json_bytes,
    execute_idempotent,
    hash_idempotency_key,
    hash_idempotency_request,
)
from core.models import (
    AppendOnlyViolation,
    AuditEvent,
    IdempotencyRecord,
    Operation,
    OperationalSetting,
    OperationalSettingRevision,
    RevisionConflict,
)
from core.operations import (
    InvalidOperationTransition,
    OperationCancellationRequested,
    OperationNotCancellable,
    cancel_operation,
    create_operation,
    finish_operation,
    request_operation_cancellation,
    start_operation,
    update_operation_progress,
)
from core.redaction import REDACTED
from core.services import ServiceContext

BOOLEAN_SETTING = register_operational_setting(
    OperationalSettingDefinition(
        key="tests.shared.enabled",
        value_type=OperationalSetting.ValueType.BOOLEAN,
        default=False,
        description="Exercise the shared operational-setting service.",
    )
)
CONCURRENT_SETTING = register_operational_setting(
    OperationalSettingDefinition(
        key="tests.shared.concurrent",
        value_type=OperationalSetting.ValueType.INTEGER,
        default=0,
        description="Exercise PostgreSQL absent-row compare-and-swap behavior.",
    )
)


def audit_context(
    *,
    actor_id: int | None = None,
    actor_ref: str = "",
) -> AuditWriteContext:
    return AuditWriteContext(
        actor_id=actor_id,
        actor_ref=actor_ref,
        execution=ExecutionAuditContext(
            request_id="request-123",
            correlation_id="correlation-456",
            job_id="job-789",
        ),
        idempotency_key_hash="a" * 64,
        source_ip_class="private",
    )


class OperationalSettingTests(TestCase):
    def test_default_and_database_sources_are_visible_and_revisioned(self) -> None:
        default = resolve_operational_setting(BOOLEAN_SETTING.key)
        self.assertEqual(default.value, False)
        self.assertEqual(default.source, "code_default")
        self.assertEqual(default.revision, 0)

        user = get_user_model().objects.create_user(username="setting-operator")
        resolved = set_operational_setting(
            key=BOOLEAN_SETTING.key,
            value=True,
            source="studio",
            expected_revision=0,
            context=audit_context(actor_id=user.pk, actor_ref="api_principal:settings-bot"),
        )

        self.assertEqual(resolved.value, True)
        self.assertEqual(resolved.source, "studio")
        self.assertEqual(resolved.revision, 1)
        revision = OperationalSettingRevision.objects.get()
        event = AuditEvent.objects.get(pk=revision.audit_event_id)
        self.assertEqual(revision.changed_by_id, user.pk)
        self.assertEqual(revision.changed_by_ref, "api_principal:settings-bot")
        self.assertEqual(revision.value, True)
        self.assertEqual(event.actor_id, user.pk)
        self.assertEqual(event.actor_ref, "api_principal:settings-bot")
        self.assertEqual(event.request_id, "request-123")
        self.assertEqual(event.correlation_id, "correlation-456")
        self.assertEqual(event.job_id, "job-789")
        self.assertEqual(event.idempotency_key_hash, "a" * 64)

    def test_stale_revision_does_not_overwrite_or_append_history(self) -> None:
        first = set_operational_setting(
            key=BOOLEAN_SETTING.key,
            value=True,
            source="studio",
            expected_revision=0,
        )

        with self.assertRaises(RevisionConflict) as caught:
            set_operational_setting(
                key=BOOLEAN_SETTING.key,
                value=False,
                source="admin_api",
                expected_revision=0,
            )

        self.assertEqual(caught.exception.actual, 1)
        self.assertEqual(resolve_operational_setting(BOOLEAN_SETTING.key), first)
        self.assertEqual(OperationalSettingRevision.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.count(), 1)

    def test_setting_and_history_roll_back_when_audit_write_fails(self) -> None:
        with mock.patch(
            "core.configuration.record_audit_event",
            side_effect=RuntimeError("audit unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "audit unavailable"):
                set_operational_setting(
                    key=BOOLEAN_SETTING.key,
                    value=True,
                    source="studio",
                    expected_revision=0,
                )

        self.assertFalse(OperationalSetting.objects.exists())
        self.assertFalse(OperationalSettingRevision.objects.exists())
        self.assertFalse(AuditEvent.objects.exists())

    def test_unregistered_secret_bearing_and_mistyped_settings_fail_closed(self) -> None:
        with self.assertRaises(InvalidOperationalSetting):
            register_operational_setting(
                OperationalSettingDefinition(
                    key="integration.api-token",
                    value_type=OperationalSetting.ValueType.STRING,
                    default="not-a-secret-store",
                    description="This must be rejected.",
                )
            )
        with self.assertRaises(InvalidOperationalSetting):
            set_operational_setting(
                key=BOOLEAN_SETTING.key,
                value=1,
                source="studio",
                expected_revision=0,
            )


class AuditPersistenceTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="audit-operator")

    def test_writer_redacts_recursively_without_mutating_input(self) -> None:
        canary = "Bearer audit-secret-canary"
        changes = {
            "safe": {"count": 3, "authorization_header": canary},
            "nested": [{"providerMessage": "safe"}, {"note": canary}],
        }
        before = json.loads(json.dumps(changes))

        event = record_audit_event(
            action="tests.audit.write",
            target_type="tests.resource",
            outcome=AuditEvent.Outcome.SUCCEEDED,
            context=audit_context(actor_id=self.user.pk),
            changes=changes,
            metadata={"management_link": "https://example.test/private"},
        )

        self.assertEqual(changes, before)
        rendered = json.dumps({"changes": event.changes, "metadata": event.metadata})
        self.assertNotIn(canary, rendered)
        self.assertNotIn("https://example.test/private", rendered)
        self.assertEqual(event.changes["safe"]["authorization_header"], REDACTED)

    def test_service_context_bridges_bounded_api_principal_attribution(self) -> None:
        service_context = ServiceContext(
            request_id="request-api",
            correlation_id="correlation-api",
            job_id="job-api",
            actor_ref="api_principal:content-sync-bot",
            idempotency_key="raw-key-must-not-enter-audit",
        )
        event = record_audit_event(
            action="tests.audit.api_principal",
            target_type="tests.resource",
            outcome=AuditEvent.Outcome.SUCCEEDED,
            context=AuditWriteContext.from_service_context(service_context),
        )

        self.assertEqual(event.actor_ref, "api_principal:content-sync-bot")
        self.assertEqual(event.request_id, "request-api")
        self.assertEqual(event.correlation_id, "correlation-api")
        self.assertEqual(event.job_id, "job-api")
        self.assertEqual(event.idempotency_key_hash, "")
        self.assertNotIn("raw-key-must-not-enter-audit", repr(event.__dict__))

    def test_audit_actor_ref_rejects_invalid_pii_url_and_credential_shapes(self) -> None:
        rejected_refs = (
            "missing-prefix",
            "user:person@example.invalid",
            "api:https://studio.example.invalid/principals/1",
            "service:ghp_abcdefghijklmnopqrstuvwxyz0123456789",
            "service:AKIAABCDEFGHIJKLMNOP",
            "service:eyJheader.payload.signature",
        )
        for actor_ref in rejected_refs:
            with self.subTest(actor_ref=actor_ref), self.assertRaises(ValueError) as caught:
                record_audit_event(
                    action="tests.audit.invalid_actor",
                    target_type="tests.resource",
                    outcome=AuditEvent.Outcome.SUCCEEDED,
                    context=AuditWriteContext(actor_ref=actor_ref),
                )
            self.assertNotIn(actor_ref, str(caught.exception))
        self.assertFalse(AuditEvent.objects.exists())

    def test_application_paths_are_append_only_but_actor_set_null_is_retained(self) -> None:
        event = record_audit_event(
            action="tests.audit.write",
            target_type="tests.resource",
            outcome=AuditEvent.Outcome.SUCCEEDED,
            context=audit_context(actor_id=self.user.pk, actor_ref="user:audit-operator"),
        )

        event.action = "tests.audit.rewrite"
        with self.assertRaises(AppendOnlyViolation):
            event.save()
        with self.assertRaises(AppendOnlyViolation):
            AuditEvent.objects.filter(pk=event.pk).update(action="tests.audit.rewrite")
        with self.assertRaises(AppendOnlyViolation):
            AuditEvent.objects.bulk_update([event], ("action",))
        with self.assertRaises(AppendOnlyViolation):
            AuditEvent.objects.filter(pk=event.pk).delete()
        with self.assertRaises(AppendOnlyViolation):
            event.delete()
        with self.assertRaises(AppendOnlyViolation):
            AuditEvent.objects.bulk_create(
                [
                    AuditEvent(
                        action="tests.audit.bypass",
                        target_type="tests.resource",
                        outcome=AuditEvent.Outcome.SUCCEEDED,
                    )
                ]
            )

        setting = set_operational_setting(
            key=BOOLEAN_SETTING.key,
            value=True,
            source="studio",
            expected_revision=0,
            context=audit_context(actor_id=self.user.pk),
        )
        history = OperationalSettingRevision.objects.get(revision=setting.revision)
        history.source = "rewritten"
        with self.assertRaises(AppendOnlyViolation):
            history.save()
        with self.assertRaises(AppendOnlyViolation):
            OperationalSettingRevision.objects.filter(pk=history.pk).update(source="rewritten")
        with self.assertRaises(AppendOnlyViolation):
            OperationalSettingRevision.objects.filter(pk=history.pk).delete()

        self.user.delete()
        event.refresh_from_db()
        self.assertIsNone(event.actor_id)
        self.assertEqual(event.actor_ref, "user:audit-operator")

    def test_setting_revision_retains_evidence_after_actor_deletion(self) -> None:
        set_operational_setting(
            key=BOOLEAN_SETTING.key,
            value=True,
            source="studio",
            expected_revision=0,
            context=audit_context(actor_id=self.user.pk, actor_ref="user:setting-operator"),
        )

        self.user.delete()
        revision = OperationalSettingRevision.objects.get()
        event = AuditEvent.objects.get()
        self.assertIsNone(revision.changed_by_id)
        self.assertIsNone(event.actor_id)
        self.assertEqual(revision.changed_by_ref, "user:setting-operator")
        self.assertEqual(event.actor_ref, "user:setting-operator")
        self.assertEqual(revision.audit_event_id, event.id)


class IdempotencyTests(TestCase):
    def test_identical_request_replays_one_result_and_conflict_is_deterministic(self) -> None:
        executions = 0

        def command() -> JsonObject:
            nonlocal executions
            executions += 1
            return {"number": executions}

        first = execute_idempotent(
            scope="tests.command",
            key="raw-key-never-stored",
            request={"a": 1, "b": 2},
            command=command,
        )
        replay = execute_idempotent(
            scope="tests.command",
            key="raw-key-never-stored",
            request={"b": 2, "a": 1},
            command=command,
        )

        self.assertFalse(first.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(first.value, replay.value)
        self.assertEqual(executions, 1)
        record = IdempotencyRecord.objects.get()
        self.assertNotIn("raw-key-never-stored", record.key_hash)
        self.assertEqual(len(record.key_hash), 64)

        with self.assertRaises(IdempotencyConflict):
            execute_idempotent(
                scope="tests.command",
                key="raw-key-never-stored",
                request={"a": 2},
                command=command,
            )
        self.assertEqual(executions, 1)

    def test_owner_failure_and_enclosing_rollback_leave_no_stranded_record(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "owner crashed"):
            execute_idempotent(
                scope="tests.crash",
                key="owner-key",
                request={},
                command=lambda: (_ for _ in ()).throw(RuntimeError("owner crashed")),
            )
        self.assertFalse(IdempotencyRecord.objects.exists())

        with self.assertRaisesRegex(RuntimeError, "outer rollback"):
            with transaction.atomic():

                def command() -> JsonObject:
                    Operation.objects.create(kind="tests.rollback")
                    return {"status": "created"}

                execute_idempotent(
                    scope="tests.rollback",
                    key="nested-key",
                    request={},
                    command=command,
                )
                raise RuntimeError("outer rollback")

        self.assertFalse(Operation.objects.exists())
        self.assertFalse(IdempotencyRecord.objects.exists())

        def retry_command() -> JsonObject:
            Operation.objects.create(kind="tests.rollback")
            return {"status": "created"}

        execute_idempotent(
            scope="tests.rollback",
            key="nested-key",
            request={},
            command=retry_command,
        )
        self.assertEqual(Operation.objects.count(), 1)

    def test_committed_in_progress_record_requires_reconciliation(self) -> None:
        scope = "tests.orphan"
        key = "owner-key"
        IdempotencyRecord.objects.create(
            scope=scope,
            key_hash=hash_idempotency_key(scope, key),
            request_hash=hash_idempotency_request(scope, {}),
        )

        with self.assertRaises(IdempotencyInProgress):
            execute_idempotent(
                scope=scope,
                key=key,
                request={},
                command=lambda: {"unexpected": True},
            )

    def test_hashes_are_canonical_fenced_and_json_is_bounded(self) -> None:
        self.assertEqual(
            canonical_json_bytes({"a": 1, "b": 2}),
            canonical_json_bytes({"b": 2, "a": 1}),
        )
        self.assertNotEqual(
            hash_idempotency_key("tests.one", "same-key"),
            hash_idempotency_key("tests.two", "same-key"),
        )
        with self.assertRaises(UnsafeJsonValue):
            canonical_json_bytes({"value": float("nan")})


class OperationLifecycleTests(TestCase):
    def test_creation_uses_ambient_origin_and_actor_snapshot_then_resets(self) -> None:
        with context_scope(
            request_id="request-origin",
            correlation_id="correlation-origin",
            job_id="job-create",
        ):
            operation = create_operation(
                kind="tests.ambient",
                cancellable=False,
                context=AuditWriteContext(actor_ref="api_principal:operation-bot"),
            )
        event = AuditEvent.objects.get(action="core.operation.create")

        self.assertEqual(operation.actor_ref, "api_principal:operation-bot")
        self.assertEqual(operation.request_id, "request-origin")
        self.assertEqual(operation.correlation_id, "correlation-origin")
        self.assertEqual(event.actor_ref, "api_principal:operation-bot")
        self.assertEqual(event.request_id, "request-origin")
        self.assertEqual(event.correlation_id, "correlation-origin")
        self.assertEqual(event.job_id, "job-create")
        self.assertIsNone(current_context().request_id)
        self.assertIsNone(current_context().correlation_id)
        self.assertIsNone(current_context().job_id)

    def test_transition_keeps_operation_origin_and_adds_ambient_worker_job_id(self) -> None:
        with context_scope(request_id="request-origin", correlation_id="correlation-origin"):
            operation = create_operation(kind="tests.worker-origin", cancellable=False)

        with context_scope(
            request_id="request-worker",
            correlation_id="correlation-worker",
            job_id="job-worker",
        ):
            start_operation(operation_id=operation.id, expected_revision=1)

        event = AuditEvent.objects.get(action="core.operation.start")
        self.assertEqual(event.request_id, "request-origin")
        self.assertEqual(event.correlation_id, "correlation-origin")
        self.assertEqual(event.job_id, "job-worker")
        self.assertIsNone(current_context().job_id)

    def test_progress_and_terminal_lifecycle_is_revisioned_and_audited(self) -> None:
        operation = create_operation(
            kind="tests.import",
            cancellable=True,
            progress_total=10,
            context=audit_context(),
        )
        operation = start_operation(operation_id=operation.id, expected_revision=1)
        operation = update_operation_progress(
            operation_id=operation.id,
            expected_revision=2,
            current=4,
            message="four complete",
        )
        operation = finish_operation(
            operation_id=operation.id,
            expected_revision=3,
            succeeded=True,
            result_summary={"completed": 10},
        )

        self.assertEqual(operation.status, Operation.Status.SUCCEEDED)
        self.assertEqual(operation.revision, 4)
        self.assertIsNotNone(operation.started_at)
        self.assertIsNotNone(operation.finished_at)
        self.assertEqual(operation.progress_current, 4)
        self.assertEqual(AuditEvent.objects.filter(target_id=operation.id).count(), 4)

    def test_stale_progress_does_not_overwrite(self) -> None:
        operation = start_operation(
            operation_id=create_operation(kind="tests.cas", cancellable=False).id,
            expected_revision=1,
        )
        update_operation_progress(
            operation_id=operation.id,
            expected_revision=2,
            current=1,
        )

        with self.assertRaises(RevisionConflict):
            update_operation_progress(
                operation_id=operation.id,
                expected_revision=2,
                current=2,
            )
        operation.refresh_from_db()
        self.assertEqual(operation.progress_current, 1)
        self.assertEqual(operation.revision, 3)

    def test_cancellation_wins_over_stale_worker_completion(self) -> None:
        operation = start_operation(
            operation_id=create_operation(kind="tests.cancel", cancellable=True).id,
            expected_revision=1,
        )
        operation = request_operation_cancellation(
            operation_id=operation.id,
            expected_revision=2,
        )

        with self.assertRaises(RevisionConflict):
            finish_operation(
                operation_id=operation.id,
                expected_revision=2,
                succeeded=True,
            )
        with self.assertRaises(OperationCancellationRequested):
            finish_operation(
                operation_id=operation.id,
                expected_revision=operation.revision,
                succeeded=True,
            )
        operation = cancel_operation(
            operation_id=operation.id,
            expected_revision=operation.revision,
        )
        self.assertEqual(operation.status, Operation.Status.CANCELLED)

    def test_invalid_cancellation_and_transition_fail_without_mutation(self) -> None:
        operation = create_operation(kind="tests.no-cancel", cancellable=False)
        with self.assertRaises(OperationNotCancellable):
            request_operation_cancellation(
                operation_id=operation.id,
                expected_revision=1,
            )
        with self.assertRaises(InvalidOperationTransition):
            finish_operation(
                operation_id=operation.id,
                expected_revision=1,
                succeeded=True,
            )
        operation.refresh_from_db()
        self.assertEqual(operation.status, Operation.Status.PENDING)
        self.assertEqual(operation.revision, 1)

    def test_result_and_error_metadata_are_redacted(self) -> None:
        operation = start_operation(
            operation_id=create_operation(kind="tests.redaction", cancellable=False).id,
            expected_revision=1,
        )
        operation = finish_operation(
            operation_id=operation.id,
            expected_revision=2,
            succeeded=False,
            result_summary={"access_token": "unsafe"},
            errors=[{"note": "Bearer operation-canary"}],
        )
        rendered = json.dumps({"result": operation.result_summary, "errors": operation.errors})
        self.assertNotIn("unsafe", rendered)
        self.assertNotIn("operation-canary", rendered)

    def test_creation_rolls_back_when_audit_write_fails(self) -> None:
        with mock.patch(
            "core.operations.record_audit_event",
            side_effect=RuntimeError("audit unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "audit unavailable"):
                create_operation(kind="tests.rollback", cancellable=False)
        self.assertFalse(Operation.objects.exists())


class SchemaConstraintTests(TransactionTestCase):
    def test_database_constraints_and_indexes_are_installed(self) -> None:
        expected = {
            AuditEvent._meta.db_table: {
                "core_audit_action_time",
                "core_audit_target_time",
                "core_audit_request",
                "core_audit_correlation",
                "core_audit_job",
                "core_audit_actor_ref",
            },
            OperationalSetting._meta.db_table: {
                "core_setting_revision_positive",
                "core_setting_definition_positive",
                "core_setting_source_key",
            },
            OperationalSettingRevision._meta.db_table: {
                "core_setting_history_revision_unique",
                "core_setting_history_revision_positive",
                "core_setting_history_key",
            },
            IdempotencyRecord._meta.db_table: {
                "core_idempotency_scope_key_unique",
                "core_idempotency_state_consistent",
                "core_idempotency_status",
                "core_idempotency_request",
            },
            Operation._meta.db_table: {
                "core_operation_revision_positive",
                "core_operation_progress_bounded",
                "core_operation_finish_state_consistent",
                "core_operation_status_time",
                "core_operation_kind_time",
                "core_operation_correlation",
            },
        }
        with connection.cursor() as cursor:
            for table, expected_names in expected.items():
                constraints = connection.introspection.get_constraints(cursor, table)
                self.assertTrue(expected_names.issubset(constraints), (table, constraints))

    def test_database_rejects_inconsistent_states(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            Operation.objects.create(kind="tests.invalid", status=Operation.Status.SUCCEEDED)
        with self.assertRaises(IntegrityError), transaction.atomic():
            IdempotencyRecord.objects.create(
                scope="tests.invalid",
                key_hash="a" * 64,
                request_hash="b" * 64,
                status=IdempotencyRecord.Status.COMPLETED,
            )

    @skipUnlessDBFeature("has_select_for_update")
    def test_postgresql_database_trigger_blocks_raw_audit_rewrite_and_delete(self) -> None:
        event = record_audit_event(
            action="tests.audit.write",
            target_type="tests.resource",
            outcome=AuditEvent.Outcome.SUCCEEDED,
        )
        setting = set_operational_setting(
            key=BOOLEAN_SETTING.key,
            value=True,
            source="studio",
            expected_revision=0,
        )
        history = OperationalSettingRevision.objects.get(revision=setting.revision)
        attempts = (
            (AuditEvent._meta.db_table, "action", "rewritten", event.id),
            (
                OperationalSettingRevision._meta.db_table,
                "source",
                "rewritten",
                history.id,
            ),
        )
        for raw_table, raw_column, new_value, object_id in attempts:
            table = connection.ops.quote_name(raw_table)
            column = connection.ops.quote_name(raw_column)
            with (
                self.assertRaises(DatabaseError),
                transaction.atomic(),
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    f"UPDATE {table} SET {column} = %s WHERE id = %s",
                    (new_value, object_id),
                )
            with (
                self.assertRaises(DatabaseError),
                transaction.atomic(),
                connection.cursor() as cursor,
            ):
                cursor.execute(f"DELETE FROM {table} WHERE id = %s", (object_id,))

    def test_migration_test_flush_exception_requires_explicit_setting_and_test_name(self) -> None:
        migration = Path(__file__).parents[1] / "migrations" / "0001_initial.py"
        source = migration.read_text()
        self.assertIn("BEFORE TRUNCATE", source)
        self.assertIn("CORE_ALLOW_APPEND_ONLY_TEST_FLUSH", source)
        self.assertIn('frozenset({"dtc_test"})', source)
        self.assertIn('database_name.startswith("test_")', source)
        self.assertIn("truncate_trigger", source)

        migration_module = import_module("core.migrations.0001_initial")

        def schema_editor_for(database_name: str):
            schema_editor = mock.Mock()
            schema_editor.connection.vendor = "postgresql"
            schema_editor.connection.settings_dict = {"NAME": database_name}
            schema_editor.connection.ops.quote_name.side_effect = lambda value: f'"{value}"'
            return schema_editor

        for database_name in ("dtc_test", "test_dtc_test", "test_dtc_test_1"):
            with self.subTest(database_name=database_name):
                schema_editor = schema_editor_for(database_name)
                with override_settings(CORE_ALLOW_APPEND_ONLY_TEST_FLUSH=True):
                    migration_module.install_append_only_guards(None, schema_editor)
                statements = "\n".join(
                    str(call.args[0]) for call in schema_editor.execute.call_args_list
                )
                self.assertNotIn("BEFORE TRUNCATE", statements)
                self.assertIn("BEFORE UPDATE OR DELETE", statements)

        for database_name in (
            "production",
            "website_test",
            "dtc_test_1",
            "dtc_test_backup",
        ):
            with self.subTest(database_name=database_name):
                schema_editor = schema_editor_for(database_name)
                with (
                    override_settings(CORE_ALLOW_APPEND_ONLY_TEST_FLUSH=True),
                    self.assertRaisesRegex(RuntimeError, "requires a Django test database"),
                ):
                    migration_module.install_append_only_guards(None, schema_editor)
                schema_editor.execute.assert_not_called()

        schema_editor = schema_editor_for("dtc_test")
        with override_settings(CORE_ALLOW_APPEND_ONLY_TEST_FLUSH=False):
            migration_module.install_append_only_guards(None, schema_editor)
        statements = "\n".join(str(call.args[0]) for call in schema_editor.execute.call_args_list)
        self.assertIn("BEFORE TRUNCATE", statements)

    @skipUnlessDBFeature("has_select_for_update")
    def test_postgresql_production_truncate_guards_reject_and_reapply(self) -> None:
        migration_module = import_module("core.migrations.0001_initial")
        trigger_names = {
            f"{function}_trigger"
            for _table, function, _retention_column in migration_module.APPEND_ONLY_GUARDS
        } | {
            f"{function}_truncate_trigger"
            for _table, function, _retention_column in migration_module.APPEND_ONLY_GUARDS
        }

        def installed_trigger_names() -> set[str]:
            placeholders = ", ".join(["%s"] * len(trigger_names))
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT tgname FROM pg_trigger "
                    f"WHERE NOT tgisinternal AND tgname IN ({placeholders})",
                    sorted(trigger_names),
                )
                return {row[0] for row in cursor.fetchall()}

        self.assertTrue(settings.CORE_ALLOW_APPEND_ONLY_TEST_FLUSH)
        try:
            with connection.schema_editor() as schema_editor:
                migration_module.remove_append_only_guards(None, schema_editor)
            with override_settings(CORE_ALLOW_APPEND_ONLY_TEST_FLUSH=False):
                with connection.schema_editor() as schema_editor:
                    migration_module.install_append_only_guards(None, schema_editor)
            self.assertEqual(installed_trigger_names(), trigger_names)

            for raw_table, _function, _retention_column in migration_module.APPEND_ONLY_GUARDS:
                table = connection.ops.quote_name(raw_table)
                with (
                    self.subTest(table=raw_table),
                    self.assertRaises(DatabaseError),
                    transaction.atomic(),
                    connection.cursor() as cursor,
                ):
                    cursor.execute(f"TRUNCATE TABLE {table}")

            with connection.schema_editor() as schema_editor:
                migration_module.remove_append_only_guards(None, schema_editor)
            self.assertEqual(installed_trigger_names(), set())

            with override_settings(CORE_ALLOW_APPEND_ONLY_TEST_FLUSH=False):
                with connection.schema_editor() as schema_editor:
                    migration_module.install_append_only_guards(None, schema_editor)
            self.assertEqual(installed_trigger_names(), trigger_names)
        finally:
            with connection.schema_editor() as schema_editor:
                migration_module.remove_append_only_guards(None, schema_editor)
                migration_module.install_append_only_guards(None, schema_editor)


class PostgreSQLConcurrencyTests(TransactionTestCase):
    databases = {"default"}

    def _run_concurrently(self, first, second):  # type: ignore[no-untyped-def]
        barrier = threading.Barrier(2)

        def run(callback):  # type: ignore[no-untyped-def]
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return callback()
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(run, callback) for callback in (first, second)]
            return [future.result(timeout=20) for future in futures]

    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_identical_idempotency_key_executes_once(self) -> None:
        executions = 0
        counter_lock = threading.Lock()

        def attempt():  # type: ignore[no-untyped-def]
            def command() -> JsonObject:
                nonlocal executions
                with counter_lock:
                    executions += 1
                    number = executions
                return {"number": number}

            return execute_idempotent(
                scope="tests.concurrent",
                key="one-key",
                request={"value": 1},
                command=command,
            )

        results = self._run_concurrently(attempt, attempt)
        self.assertEqual(executions, 1)
        self.assertEqual([result.value for result in results], [{"number": 1}, {"number": 1}])
        self.assertEqual(sorted(result.replayed for result in results), [False, True])

    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_missing_setting_compare_and_swap_has_one_winner(self) -> None:
        def attempt(value: int) -> tuple[str, int]:
            try:
                result = set_operational_setting(
                    key=CONCURRENT_SETTING.key,
                    value=value,
                    source="tests",
                    expected_revision=0,
                )
                return ("won", result.revision)
            except RevisionConflict as error:
                return ("conflict", error.actual)

        results = self._run_concurrently(lambda: attempt(1), lambda: attempt(2))
        self.assertEqual(sorted(result[0] for result in results), ["conflict", "won"])
        self.assertEqual(OperationalSettingRevision.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.count(), 1)

    @skipUnlessDBFeature("has_select_for_update")
    def test_operation_finish_and_cancel_request_race_has_one_revision_winner(self) -> None:
        operation = start_operation(
            operation_id=create_operation(kind="tests.race", cancellable=True).id,
            expected_revision=1,
        )

        def finish() -> str:
            try:
                finish_operation(
                    operation_id=operation.id,
                    expected_revision=2,
                    succeeded=True,
                )
                return "finished"
            except (RevisionConflict, OperationCancellationRequested):
                return "lost"

        def cancel() -> str:
            try:
                request_operation_cancellation(
                    operation_id=operation.id,
                    expected_revision=2,
                )
                return "cancel-requested"
            except (RevisionConflict, InvalidOperationTransition):
                return "lost"

        results = self._run_concurrently(finish, cancel)
        self.assertIn("lost", results)
        operation.refresh_from_db()
        self.assertEqual(operation.revision, 3)
        self.assertTrue(
            operation.status == Operation.Status.SUCCEEDED
            or operation.cancellation_requested_at is not None
        )
