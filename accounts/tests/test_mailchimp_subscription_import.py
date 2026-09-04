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
import tempfile
from pathlib import Path

from django.test import TestCase

from accounts.models import CustomUser
from accounts.services.mailchimp_subscription_import import (
    EMAIL_COLUMN,
    import_mailchimp_subscriptions,
)


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

    def _run(self, *, subscribed=None, **kwargs):
        if subscribed is not None:
            self.subscribed_path.unlink(missing_ok=True)
            self.subscribed_path = _write_csv(subscribed)
        return import_mailchimp_subscriptions(subscribed=self.subscribed_path, **kwargs)

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
        user = CustomUser.objects.create(
            username="untouched", email="untouched@example.invalid"
        )
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
        result = self._run(
            subscribed=[{EMAIL_COLUMN: "dryrun@example.invalid"}], apply=False
        )
        user.refresh_from_db()
        self.assertFalse(user.newsletter_subscribed)  # unchanged
        self.assertEqual(result.subscribed.matched_rows, 1)
        self.assertEqual(result.subscribed.accounts_changed, 1)  # "would change"
        self.assertFalse(result.applied)
