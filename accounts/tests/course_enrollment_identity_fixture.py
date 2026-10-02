"""Subprocess-only exact history contract; use the real model whenever installed.

Synthetic schema mirrors package ce17ad8 curriculum migration 0006. The ordinary
suite never imports this module, so registry and import-time catalog stay isolated.
"""

from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import patch

from django.apps import apps
from django.conf import settings
from django.core.exceptions import FieldDoesNotExist
from django.db import connection, models
from django.test import TransactionTestCase

installed_history = apps.get_app_config("cb_curriculum").models.get("courseenrollment")
History: type[models.Model]
SYNTHETIC = installed_history is None
if installed_history is None:

    class CourseEnrollment(models.Model):
        id = models.BigAutoField(primary_key=True, auto_created=True, serialize=False)
        user = models.ForeignKey(
            settings.AUTH_USER_MODEL,
            on_delete=models.CASCADE,
            related_name="curriculum_course_enrollments",
        )
        course = models.ForeignKey(
            "cb_curriculum.Course", on_delete=models.CASCADE, related_name="course_enrollments"
        )
        enrolled_at = models.DateTimeField(auto_now_add=True)
        unenrolled_at = models.DateTimeField(null=True, blank=True)
        source = models.CharField(
            max_length=20,
            default="manual",
            choices=[
                ("manual", "Manual"),
                ("auto_progress", "Auto (first lesson complete)"),
                ("admin", "Admin (Studio)"),
            ],
        )

        class Meta:
            app_label = "cb_curriculum"
            ordering = ("-enrolled_at",)
            constraints = [
                models.UniqueConstraint(
                    fields=("user", "course"),
                    condition=models.Q(unenrolled_at__isnull=True),
                    name="cb_course_enroll_active_uq",
                )
            ]

    History = CourseEnrollment
else:
    History = installed_history


# Resolve the real consumer imports only after the subprocess registry is complete.
from community_base.curriculum.models import Course  # noqa: E402

from accounts.identity_inventory import (  # noqa: E402
    ACCOUNT_RELATIONS,
    account_inventory,
    append_only_relation_keys,
    relationship_evidence,
    relationship_row_identities,
    stale_account_relations,
    unclassified_account_relations,
)
from accounts.identity_relations import installed_account_relations  # noqa: E402
from accounts.tests.test_account_reconciliation import mapping_document  # noqa: E402
from accounts.tests.test_single_identity import create_verified_user  # noqa: E402
from accounts_ext.models import (  # noqa: E402
    AccountIdentityAlias,
    AccountIdentityQuarantine,
    AccountReconciliationRun,
    IdentityState,
)
from courses.models import LearnerProfile  # noqa: E402
from scripts.prod.account_reconciliation import (  # noqa: E402
    ReconciliationBlocked,
    apply_reviewed_mapping,
    parse_mapping_document,
)

KEY = "cb_curriculum.CourseEnrollment.user"
INSTANT = datetime(2026, 1, 2, tzinfo=UTC)


class CourseEnrollmentHistoryTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if SYNTHETIC:
            with connection.schema_editor() as editor:
                editor.create_model(History)

    @classmethod
    def tearDownClass(cls):
        if SYNTHETIC:
            with connection.schema_editor() as editor:
                editor.delete_model(History)
            apps.all_models["cb_curriculum"].pop("courseenrollment")
            apps.clear_cache()
        super().tearDownClass()

    def setUp(self):
        self.source = create_verified_user(
            username="history-source",
            email="History@Example.Invalid",
            certificate_name="Synthetic history certificate",
        )
        self.survivor = create_verified_user(
            username="history-survivor", email="history@example.invalid"
        )
        self.course = Course.objects.create(slug="history-contract", title="Synthetic history")
        self.plan = parse_mapping_document(
            mapping_document(
                source=self.source,
                survivor=self.survivor,
                field_decisions={"certificate_name": "source"},
            )
        )

    def add_history(self, user, course=None, *, ended=False, source="manual"):
        row = History._base_manager.create(user=user, course=course or self.course, source=source)
        end = None
        if ended:
            end = INSTANT + timedelta(days=1)
        History._base_manager.filter(pk=row.pk).update(enrolled_at=INSTANT, unenrolled_at=end)
        return row

    def rows(self):
        return list(
            History._base_manager.order_by("pk").values_list(
                "pk", "user_id", "course_id", "enrolled_at", "unenrolled_at", "source"
            )
        )

    def test_inventory_and_strict_evidence_share_the_history_relation(self):
        row = self.add_history(self.source)
        specs = []
        for spec in ACCOUNT_RELATIONS:
            if spec.key == KEY:
                specs.append(spec)
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].handling, "reparent")
        matching = []
        for relation in account_inventory()["dependent_relations"]:
            if relation["model_label"] == History._meta.label:
                matching.append(relation)
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["table"], History._meta.db_table)
        self.assertEqual(
            matching[0]["column"], cast(models.ForeignKey, History._meta.get_field("user")).column
        )
        aliases = {self.source.pk: self.survivor.pk}
        counts, checksums = relationship_evidence(alias_overrides=aliases)
        self.assertEqual(counts[KEY], 1)
        self.assertEqual(checksums, relationship_evidence(alias_overrides=aliases)[1])
        self.assertEqual(
            relationship_row_identities(alias_overrides=aliases)[KEY],
            frozenset({(str(row.pk), self.survivor.pk)}),
        )
        self.assertNotIn(KEY, append_only_relation_keys())
        self.assertEqual(stale_account_relations(), [])
        self.assertEqual(unclassified_account_relations(), [])

    def test_reviewed_merge_preserves_active_ended_and_replay_history(self):
        self.add_history(self.source, ended=True, source="admin")
        self.add_history(self.survivor, ended=True)
        self.add_history(self.source, source="auto_progress")
        other = Course.objects.create(slug="other-history", title="Other history")
        self.add_history(self.survivor, other)
        self.add_history(self.source, other, ended=True)
        before = self.rows()
        aliases = {self.source.pk: self.survivor.pk}
        evidence = relationship_evidence(alias_overrides=aliases)
        identities = relationship_row_identities(alias_overrides=aliases)[KEY]
        apply_reviewed_mapping(self.plan)
        expected = []
        for row_id, user_id, *values in before:
            expected.append((row_id, aliases.get(user_id, user_id), *values))
        self.assertEqual(self.rows(), expected)
        after = relationship_evidence()
        self.assertEqual(after[0][KEY], evidence[0][KEY])
        self.assertEqual(after[1][KEY], evidence[1][KEY])
        self.assertEqual(relationship_row_identities()[KEY], identities)
        self.assertTrue(apply_reviewed_mapping(self.plan)["idempotent_replay"])
        self.assertEqual(self.rows(), expected)

    def business_state(self):
        return {
            "users": list(type(self.source).objects.order_by("pk").values()),
            "aliases": list(AccountIdentityAlias.objects.values()),
            "runs": list(AccountReconciliationRun.objects.values()),
            "history": self.rows(),
            "profiles": list(LearnerProfile.objects.values()),
            "identity_states": list(IdentityState.objects.values()),
            "staff_sessions": list(apps.get_model("core.StaffSession").objects.values()),
        }

    def test_active_collision_rolls_back_all_business_writes_and_is_redacted(self):
        self.add_history(self.source, source="admin")
        self.add_history(self.survivor, source="auto_progress")
        # This relation precedes CourseEnrollment in the real reparent loop.
        from core.models import StaffSession

        StaffSession.objects.create(user=self.source, authenticated_at=INSTANT)
        before = self.business_state()
        identities = relationship_row_identities()
        with self.assertRaises(ReconciliationBlocked) as denied:
            apply_reviewed_mapping(self.plan)
        self.assertEqual(self.business_state(), before)
        after_identities = relationship_row_identities()
        new_audits = after_identities.pop("core.AuditEvent.actor")
        old_audits = identities.pop("core.AuditEvent.actor")
        self.assertEqual(after_identities, identities)
        self.assertTrue(old_audits.issubset(new_audits))
        self.assertEqual(len(new_audits), len(old_audits) + 1)
        self.assertEqual(AccountIdentityQuarantine.objects.count(), 1)
        self.assertIn("reconciliation_integrity_conflict", str(denied.exception.conflicts))
        self.assertNotIn("example.invalid", str(denied.exception.conflicts))
        self.assertNotIn("UNIQUE", str(denied.exception.conflicts))

    def test_only_optional_model_absence_is_accepted(self):
        config = apps.get_app_config("cb_curriculum")
        with patch.dict(config.models) as registry:
            registry.pop("courseenrollment")
            keys = [spec.key for spec in installed_account_relations()]
            self.assertNotIn(KEY, keys)
            registry.pop("enrollment")
            with self.assertRaises(LookupError):
                installed_account_relations()

    def test_present_model_field_drift_and_stale_mandatory_spec_fail_closed(self):
        user_field = cast(models.ForeignKey, History._meta.get_field("user"))
        with patch.object(user_field, "name", "renamed_owner"):
            History._meta.__dict__.pop("_forward_fields_map", None)
            with self.assertRaises(FieldDoesNotExist):
                installed_account_relations()
        History._meta.__dict__.pop("_forward_fields_map", None)
        with patch.object(user_field, "related_model", Course):
            with self.assertRaises(LookupError):
                installed_account_relations()
        spec = ACCOUNT_RELATIONS[0]
        with patch.object(type(spec), "key", "accounts.User_groups.missing"):
            self.assertIn("accounts.User_groups.missing", stale_account_relations())
