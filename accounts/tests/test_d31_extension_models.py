"""D3.1: the extension models, their data copy and the contract.

Covers what the field move ships: ``courses.LearnerProfile`` and
``accounts_ext.IdentityState`` carry the moved fields with their shapes
mirrored verbatim, the four identity evidence models kept their physical
tables while their app registration moved, the identity save-path invariant
that used to live on ``User.save`` is preserved by the extension signal,
and -- since the contract phase -- the user model declares none of the twelve
moved fields and the conditional unique constraint is enforced on
``IdentityState`` alone.

The "mirrored verbatim" check reads the original field definitions out of the
migration state just before the contract migration rather than restating them,
so a drift on either side fails even though the columns are gone from the live
model.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.migrations.loader import MigrationLoader
from django.db import connection
from django.test import TestCase

from accounts_ext.models import IdentityState
from courses.models import LearnerProfile, ensure_learner_profile, learner_profile_for

MOVED_COURSE_FIELDS = (
    "role",
    "certificate_name",
    "country",
    "region",
    "registration_role",
    "github_url",
    "linkedin_url",
    "personal_website_url",
    "about_me",
    "dark_mode",
)
MOVED_IDENTITY_FIELDS = ("normalized_email", "identity_state")

#: The last migration state in which the user model still declared all twelve.
PRE_CONTRACT_MIGRATION = ("accounts", "0007_move_identity_models_state")

COMPARED_ATTRIBUTES = ("max_length", "null", "blank", "choices", "help_text")


def _pre_contract_user_fields():
    loader = MigrationLoader(connection)
    state = loader.project_state([PRE_CONTRACT_MIGRATION])
    model = state.apps.get_model("accounts", "User")
    return {field.name: field for field in model._meta.get_fields()}


class ExtensionModelsExistTestCase(TestCase):
    def _assert_mirrors_the_original(self, model, field_names):
        originals = _pre_contract_user_fields()
        moved_fields = {field.name: field for field in model._meta.get_fields()}
        for field_name in field_names:
            self.assertIn(field_name, moved_fields)
            moved, original = moved_fields[field_name], originals[field_name]
            self.assertEqual(moved.get_default(), original.get_default(), field_name)
            for attribute in COMPARED_ATTRIBUTES:
                self.assertEqual(
                    getattr(moved, attribute),
                    getattr(original, attribute),
                    f"{field_name}.{attribute} drifted from the column it moved from",
                )

    def test_learner_profile_mirrors_the_columns_it_received(self):
        self._assert_mirrors_the_original(LearnerProfile, MOVED_COURSE_FIELDS)
        related = LearnerProfile._meta.get_field("user")
        self.assertEqual(related.remote_field.related_name, "learner_profile")

    def test_identity_state_mirrors_the_columns_it_received(self):
        self._assert_mirrors_the_original(IdentityState, MOVED_IDENTITY_FIELDS)
        related = IdentityState._meta.get_field("user")
        self.assertEqual(related.remote_field.related_name, "identity")

    def test_the_user_model_no_longer_declares_the_moved_fields(self):
        field_names = {field.name for field in get_user_model()._meta.get_fields()}
        for field_name in (*MOVED_COURSE_FIELDS, *MOVED_IDENTITY_FIELDS):
            self.assertNotIn(field_name, field_names)

    def test_the_moved_constraint_left_the_user_model(self):
        constraint_names = {
            constraint.name for constraint in get_user_model()._meta.constraints
        }
        self.assertNotIn("accounts_active_normalized_email_unique", constraint_names)

    def test_the_dtc_only_columns_stayed(self):
        # Their disposition belongs to the shared-model adoption, not here.
        field_names = {field.name for field in get_user_model()._meta.get_fields()}
        for field_name in (
            "username",
            "newsletter_subscribed",
            "home_dismissals",
            "newsletter_preference_changed_at",
            "preferred_timezone",
        ):
            self.assertIn(field_name, field_names)

    def test_moved_identity_models_kept_their_tables(self):
        from accounts_ext.models import (
            AccountIdentityAlias,
            AccountIdentityQuarantine,
            AccountReconciliationRun,
            CmpLearnerImportProgress,
        )

        self.assertEqual(AccountIdentityAlias._meta.db_table, "accounts_accountidentityalias")
        self.assertEqual(
            AccountIdentityQuarantine._meta.db_table, "accounts_accountidentityquarantine"
        )
        self.assertEqual(
            AccountReconciliationRun._meta.db_table, "accounts_accountreconciliationrun"
        )
        self.assertEqual(
            CmpLearnerImportProgress._meta.db_table, "accounts_cmplearnerimportprogress"
        )


class LearnerProfileAccessorsTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="profile-accessor",
            email="profile-accessor@example.com",
            password="testpass123",
        )

    def test_a_user_without_a_row_reads_as_missing(self):
        self.assertIsNone(learner_profile_for(self.user))

    def test_ensure_creates_the_row_once_with_the_moved_defaults(self):
        profile = ensure_learner_profile(self.user)
        self.assertEqual(profile.role, "student")
        self.assertFalse(profile.dark_mode)
        self.assertIs(ensure_learner_profile(self.user).pk, profile.pk)
        self.assertEqual(LearnerProfile.objects.filter(user=self.user).count(), 1)


class SavePathInvariantTestCase(TestCase):
    """The ``normalized_email`` invariant survives the move to IdentityState."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="invariant-user",
            email="invariant@example.com",
            password="testpass123",
        )

    def _row(self):
        return IdentityState.objects.get(user=self.user)

    def test_creation_syncs_the_normalized_key(self):
        self.assertEqual(self._row().normalized_email, "invariant@example.com")

    def test_a_full_save_resyncs_the_normalized_key(self):
        self.user.email = "Changed@Example.COM"
        self.user.save()
        self.assertEqual(self._row().normalized_email, "changed@example.com")

    def test_a_partial_email_save_resyncs_the_normalized_key(self):
        self.user.email = "Partial@Example.COM"
        self.user.save(update_fields=["email"])
        self.assertEqual(self._row().normalized_email, "partial@example.com")

    def test_a_partial_save_without_email_does_not_touch_the_row(self):
        # Mirror of the old persistence rule: the normalized column was only
        # rewritten when the save persisted it.
        IdentityState.objects.filter(user=self.user).update(
            normalized_email="held@example.com"
        )
        self.user.preferred_timezone = "Europe/Berlin"
        self.user.save(update_fields=["preferred_timezone"])
        self.assertEqual(self._row().normalized_email, "held@example.com")


class IdentityStateConstraintTestCase(TestCase):
    """The moved conditional unique constraint, now enforced on IdentityState."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="constraint-user",
            email="constraint@example.com",
            password="testpass123",
        )

    def test_the_constraint_is_declared_on_identity_state(self):
        constraint_names = {constraint.name for constraint in IdentityState._meta.constraints}
        self.assertIn("accounts_active_normalized_email_unique", constraint_names)

    def test_two_active_rows_with_one_normalized_email_are_rejected(self):
        other = get_user_model().objects.create_user(
            username="constraint-other",
            email="other@example.com",
            password="testpass123",
        )
        IdentityState.objects.filter(user=other).update(
            normalized_email="same@example.com",
            identity_state=IdentityState.States.ACTIVE,
        )
        row = IdentityState.objects.get(user=self.user)
        row.normalized_email = "same@example.com"
        row.identity_state = IdentityState.States.ACTIVE
        with self.assertRaises(Exception), transaction.atomic():
            row.save()

    def test_duplicate_normalized_email_is_allowed_outside_active(self):
        other = get_user_model().objects.create_user(
            username="constraint-legacy",
            email="legacy@example.com",
            password="testpass123",
        )
        row = IdentityState.objects.get(user=self.user)
        row.normalized_email = "legacy@example.com"
        row.identity_state = IdentityState.States.LEGACY
        row.save()
        other_row = IdentityState.objects.get(user=other)
        other_row.normalized_email = "legacy@example.com"
        other_row.save()
