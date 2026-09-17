"""D3.1a: the additive extension models and their data copy.

Covers what the expand half of plan issue D3.1 ships: ``courses.LearnerProfile``
and ``accounts_ext.IdentityState`` exist with the moved fields' shapes mirrored
verbatim, the four identity evidence models kept their physical tables while
their app registration moved, and the identity save-path invariant that used to
live on ``CustomUser.save`` is preserved by the extension signal.

No reader is switched and no field is removed by this phase, so the user model
still declares the moved fields and both copies stay in step.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
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


class ExtensionModelsExistTestCase(TestCase):
    def test_learner_profile_exists_with_mirrored_shapes(self):
        profile_fields = {field.name: field for field in LearnerProfile._meta.get_fields()}
        user_fields = {field.name: field for field in get_user_model()._meta.get_fields()}
        for field_name in MOVED_COURSE_FIELDS:
            self.assertIn(field_name, profile_fields)
            moved, original = profile_fields[field_name], user_fields[field_name]
            self.assertEqual(moved.get_default(), original.get_default(), field_name)
            self.assertEqual(moved.max_length, original.max_length, field_name)
            self.assertEqual(moved.null, original.null, field_name)
            self.assertEqual(moved.blank, original.blank, field_name)
            self.assertEqual(moved.choices, original.choices, field_name)
            self.assertEqual(moved.help_text, original.help_text, field_name)
        related = LearnerProfile._meta.get_field("user")
        self.assertEqual(related.remote_field.related_name, "learner_profile")

    def test_identity_state_exists_with_moved_shapes(self):
        state_fields = {field.name: field for field in IdentityState._meta.get_fields()}
        user_fields = {field.name: field for field in get_user_model()._meta.get_fields()}
        for field_name in MOVED_IDENTITY_FIELDS:
            self.assertIn(field_name, state_fields)
            moved, original = state_fields[field_name], user_fields[field_name]
            self.assertEqual(moved.get_default(), original.get_default(), field_name)
            self.assertEqual(moved.max_length, original.max_length, field_name)
            self.assertEqual(moved.null, original.null, field_name)
            self.assertEqual(moved.choices, original.choices, field_name)
        related = IdentityState._meta.get_field("user")
        self.assertEqual(related.remote_field.related_name, "identity")

    def test_the_user_model_still_declares_the_moved_fields(self):
        # This phase is purely additive: the contract phase (D3.1d) removes them.
        field_names = {field.name for field in get_user_model()._meta.get_fields()}
        for field_name in (*MOVED_COURSE_FIELDS, *MOVED_IDENTITY_FIELDS):
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

    def test_the_extension_row_agrees_with_the_user_column(self):
        # Both copies are live during the expand window.
        self.user.refresh_from_db()
        self.assertEqual(self._row().normalized_email, self.user.normalized_email)
        self.assertEqual(self._row().identity_state, self.user.identity_state)
