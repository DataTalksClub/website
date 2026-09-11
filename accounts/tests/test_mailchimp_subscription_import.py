"""Tests for the Mailchimp newsletter-subscription importer.

Every email used here is a synthetic ``example.invalid`` address -- no real
Mailchimp export is read in this suite. Only the subscribed export is in
scope: the owner narrowed the original three-file design down to "set
subscribed only to those who are subscribed in mailchimp" -- Mailchimp's
separate unsubscribed/cleaned exports are never opened by this importer at
all, so there is nothing here proving a ``False`` write; that write does not
exist.
"""

from __future__ import annotations

import csv
import hashlib
import tempfile
from datetime import date
from pathlib import Path

from django.test import TestCase

from accounts.models import CustomUser, MailchimpSubscriptionImportRun
from accounts.services.mailchimp_subscription_import import (
    EMAIL_COLUMN,
    import_mailchimp_subscriptions,
)

# The operator-declared snapshot date every test run records.
AS_OF = date(2026, 9, 2)


def _write_csv(rows: list[dict[str, str]]) -> Path:
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, newline="", encoding="utf-8"
    )
    fieldnames = [EMAIL_COLUMN, "Name", "MEMBER_RATING", "TAGS"]
    writer = csv.DictWriter(tmp, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row.get(field, "") for field in fieldnames})
    tmp.close()
    return Path(tmp.name)


class MailchimpSubscriptionImportTests(TestCase):
    def setUp(self):
        self.subscribed_path = _write_csv([])
        self.addCleanup(self.subscribed_path.unlink, missing_ok=True)

    def _run(self, *, subscribed=None, as_of=AS_OF, **kwargs):
        if subscribed is not None:
            self.subscribed_path.unlink(missing_ok=True)
            self.subscribed_path = _write_csv(subscribed)
        return import_mailchimp_subscriptions(
            subscribed=self.subscribed_path, as_of=as_of, **kwargs
        )

    def test_new_account_defaults_to_subscribed(self):
        user = CustomUser.objects.create(username="fresh", email="fresh@example.invalid")
        self.assertTrue(user.newsletter_subscribed)

    def test_subscribed_match_sets_true_explicitly(self):
        user = CustomUser.objects.create(
            username="subbed", email="subbed@example.invalid", newsletter_subscribed=False
        )
        result = self._run(subscribed=[{EMAIL_COLUMN: "subbed@example.invalid"}])
        user.refresh_from_db()
        self.assertTrue(user.newsletter_subscribed)
        self.assertEqual(result.subscribed.matched_rows, 1)
        self.assertEqual(result.subscribed.accounts_changed, 1)
        self.assertEqual(result.subscribed.unmatched_rows, 0)

    def test_subscribed_match_against_default_true_is_a_reported_no_op(self):
        user = CustomUser.objects.create(username="already", email="already@example.invalid")
        self.assertTrue(user.newsletter_subscribed)
        result = self._run(subscribed=[{EMAIL_COLUMN: "already@example.invalid"}])
        user.refresh_from_db()
        self.assertTrue(user.newsletter_subscribed)
        self.assertEqual(result.subscribed.matched_rows, 1)
        # Already True, so bulk_update has nothing to change -- confirming
        # the default rather than toggling it.
        self.assertEqual(result.subscribed.accounts_changed, 0)

    def test_no_match_anywhere_leaves_account_untouched_at_default(self):
        user = CustomUser.objects.create(username="untouched", email="untouched@example.invalid")
        result = self._run(subscribed=[{EMAIL_COLUMN: "someone-else@example.invalid"}])
        user.refresh_from_db()
        self.assertTrue(user.newsletter_subscribed)
        self.assertEqual(result.subscribed.unmatched_rows, 1)
        self.assertEqual(result.subscribed.matched_rows, 0)

    def test_mailchimp_row_with_no_account_creates_nothing(self):
        before = CustomUser.objects.count()
        result = self._run(
            subscribed=[{EMAIL_COLUMN: "ghost-subscribed@example.invalid"}],
        )
        self.assertEqual(CustomUser.objects.count(), before)
        self.assertEqual(result.subscribed.unmatched_rows, 1)

    def test_matching_is_case_insensitive(self):
        user = CustomUser.objects.create(
            username="mixedcase",
            email="MixedCase@Example.Invalid",
            newsletter_subscribed=False,
        )
        result = self._run(subscribed=[{EMAIL_COLUMN: "mixedcase@example.invalid"}])
        user.refresh_from_db()
        self.assertTrue(user.newsletter_subscribed)
        self.assertEqual(result.subscribed.matched_rows, 1)

    def test_duplicate_accounts_sharing_an_email_are_all_updated(self):
        first = CustomUser.objects.create(
            username="dupe1", email="dupe@example.invalid", newsletter_subscribed=False
        )
        second = CustomUser.objects.create(
            username="dupe2", email="dupe@example.invalid", newsletter_subscribed=False
        )
        result = self._run(subscribed=[{EMAIL_COLUMN: "dupe@example.invalid"}])
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertTrue(first.newsletter_subscribed)
        self.assertTrue(second.newsletter_subscribed)
        self.assertEqual(result.subscribed.accounts_changed, 2)

    def test_rerun_is_idempotent(self):
        subscribed_user = CustomUser.objects.create(
            username="subbed2", email="subbed2@example.invalid", newsletter_subscribed=False
        )
        first = self._run(subscribed=[{EMAIL_COLUMN: "subbed2@example.invalid"}])
        self.assertEqual(first.subscribed.accounts_changed, 1)

        second = self._run()  # re-reads the same file, unchanged
        self.assertEqual(second.subscribed.matched_rows, 1)
        self.assertEqual(second.subscribed.accounts_changed, 0)

        subscribed_user.refresh_from_db()
        self.assertTrue(subscribed_user.newsletter_subscribed)

    def test_dry_run_reports_without_writing(self):
        user = CustomUser.objects.create(
            username="dryrun", email="dryrun@example.invalid", newsletter_subscribed=False
        )
        result = self._run(subscribed=[{EMAIL_COLUMN: "dryrun@example.invalid"}], apply=False)
        user.refresh_from_db()
        self.assertFalse(user.newsletter_subscribed)  # unchanged
        self.assertEqual(result.subscribed.matched_rows, 1)
        self.assertEqual(result.subscribed.accounts_changed, 1)  # "would change"
        self.assertFalse(result.applied)


class LocalDecisionCutoffTests(TestCase):
    """A recorded local preference decision outranks any snapshot (BE-15).

    The importer's subscribed file is authoritative for the *default*; the
    moment a member has changed the preference in this application, the
    local decision wins and no replay -- however stale or fresh -- may
    override it.  These tests also pin the stamp's lifecycle: set by an
    in-app save that changes the value, never by creation, and never by the
    import's own ``bulk_update`` writes.
    """

    def _import_rows(self, rows, **kwargs):
        path = _write_csv(rows)
        self.addCleanup(path.unlink, missing_ok=True)
        return import_mailchimp_subscriptions(subscribed=path, as_of=AS_OF, **kwargs)

    def _decide_locally(self, user, *, subscribed: bool) -> None:
        user.refresh_from_db()
        user.newsletter_subscribed = subscribed
        user.save()
        user.refresh_from_db()

    def test_an_in_app_preference_change_sets_the_decision_stamp(self) -> None:
        user = CustomUser.objects.create(username="decided", email="decided@example.invalid")
        self.assertIsNone(user.newsletter_preference_changed_at)

        self._decide_locally(user, subscribed=False)

        self.assertIsNotNone(user.newsletter_preference_changed_at)
        self.assertFalse(user.newsletter_subscribed)

    def test_creation_is_not_a_local_decision(self) -> None:
        user = CustomUser.objects.create(
            username="created-false",
            email="created-false@example.invalid",
            newsletter_subscribed=False,
        )

        self.assertIsNone(user.newsletter_preference_changed_at)

    def test_the_stamp_survives_an_update_fields_save(self) -> None:
        user = CustomUser.objects.create(
            username="update-fields", email="update-fields@example.invalid"
        )
        self._decide_locally(user, subscribed=False)
        first_stamp = user.newsletter_preference_changed_at

        user.newsletter_subscribed = True
        user.save(update_fields=["newsletter_subscribed"])

        user.refresh_from_db()
        self.assertIsNotNone(user.newsletter_preference_changed_at)
        self.assertNotEqual(user.newsletter_preference_changed_at, first_stamp)

    def test_a_local_opt_out_survives_a_stale_replay(self) -> None:
        user = CustomUser.objects.create(username="opted-out", email="opted-out@example.invalid")
        self._decide_locally(user, subscribed=False)

        result = self._import_rows([{EMAIL_COLUMN: "opted-out@example.invalid"}])

        self.assertEqual(result.subscribed.skipped_local_decisions, 1)
        self.assertEqual(result.subscribed.accounts_changed, 0)
        user.refresh_from_db()
        # The newer decision stands: the replay did not resurrect the
        # subscription.
        self.assertFalse(user.newsletter_subscribed)

    def test_the_imports_own_writes_are_not_local_decisions(self) -> None:
        user = CustomUser.objects.create(
            username="migrated",
            email="migrated@example.invalid",
            newsletter_subscribed=False,
        )

        self._import_rows([{EMAIL_COLUMN: "migrated@example.invalid"}])

        user.refresh_from_db()
        self.assertTrue(user.newsletter_subscribed)
        # The migration write carries no decision stamp: it is not something
        # the member chose, so a later local decision can still be made (and
        # would then win).
        self.assertIsNone(user.newsletter_preference_changed_at)

    def test_a_stamped_account_is_skipped_even_when_the_value_already_matches(
        self,
    ) -> None:
        # Opted out at creation, then locally re-subscribed: the flip makes
        # the stamp real (a no-op save is not a decision), and the import
        # must still defer to it even though the value now agrees with the
        # snapshot.
        user = CustomUser.objects.create(
            username="locally-subscribed",
            email="locally-subscribed@example.invalid",
            newsletter_subscribed=False,
        )
        self._decide_locally(user, subscribed=True)

        result = self._import_rows([{EMAIL_COLUMN: "locally-subscribed@example.invalid"}])

        # The value already agrees with the snapshot, but the skip rule is
        # about authority, not effect: the local decision is what governs.
        self.assertEqual(result.subscribed.skipped_local_decisions, 1)
        self.assertEqual(result.subscribed.accounts_changed, 0)


class ImportProvenanceTests(TestCase):
    """Every invocation records which snapshot ran, when, and with what effect."""

    def test_an_applied_run_records_digest_as_of_and_counts(self) -> None:
        self.subscribed_path = _write_csv([{EMAIL_COLUMN: "provenance@example.invalid"}])
        self.addCleanup(self.subscribed_path.unlink, missing_ok=True)
        user = CustomUser.objects.create(
            username="provenance",
            email="provenance@example.invalid",
            newsletter_subscribed=False,
        )

        result = import_mailchimp_subscriptions(
            subscribed=self.subscribed_path, as_of=date(2026, 9, 2)
        )

        run = MailchimpSubscriptionImportRun.objects.get(pk=result.run_id)
        self.assertEqual(
            run.source_sha256,
            hashlib.sha256(self.subscribed_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(run.source_name, self.subscribed_path.name)
        self.assertEqual(run.source_bytes, len(self.subscribed_path.read_bytes()))
        self.assertEqual(run.as_of, date(2026, 9, 2))
        self.assertTrue(run.applied)
        self.assertEqual(run.report["matched_rows"], 1)
        self.assertEqual(run.report["accounts_changed"], 1)
        user.refresh_from_db()
        self.assertTrue(user.newsletter_subscribed)

    def test_a_dry_run_is_recorded_as_unapplied(self) -> None:
        self.subscribed_path = _write_csv([{EMAIL_COLUMN: "dryrun2@example.invalid"}])
        self.addCleanup(self.subscribed_path.unlink, missing_ok=True)
        CustomUser.objects.create(
            username="dryrun2",
            email="dryrun2@example.invalid",
            newsletter_subscribed=False,
        )

        result = import_mailchimp_subscriptions(
            subscribed=self.subscribed_path,
            as_of=date(2026, 9, 2),
            apply=False,
        )

        run = MailchimpSubscriptionImportRun.objects.get(pk=result.run_id)
        self.assertFalse(run.applied)
        self.assertEqual(run.report["accounts_changed"], 1)  # "would change"
        self.assertEqual(MailchimpSubscriptionImportRun.objects.count(), 1)

    def test_repeated_same_snapshot_runs_converge(self) -> None:
        self.subscribed_path = _write_csv([{EMAIL_COLUMN: "converge@example.invalid"}])
        self.addCleanup(self.subscribed_path.unlink, missing_ok=True)
        CustomUser.objects.create(
            username="converge",
            email="converge@example.invalid",
            newsletter_subscribed=False,
        )

        first = import_mailchimp_subscriptions(
            subscribed=self.subscribed_path, as_of=date(2026, 9, 2)
        )
        second = import_mailchimp_subscriptions(
            subscribed=self.subscribed_path, as_of=date(2026, 9, 2)
        )

        self.assertEqual(first.subscribed.accounts_changed, 1)
        self.assertEqual(second.subscribed.accounts_changed, 0)
        # Two runs, two provenance rows: history, not an upsert.
        self.assertEqual(MailchimpSubscriptionImportRun.objects.count(), 2)
