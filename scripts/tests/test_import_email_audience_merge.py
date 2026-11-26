"""The email audience merge follows _docs/architecture/email-audience-merge.md."""

from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

import scripts.prod
from scripts.prod import import_email_audience_merge as merge

T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = datetime(2026, 6, 1, tzinfo=UTC)


def _cmp_export(path: Path, registrations: list[tuple], events: list[tuple] | None = None) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE courses_registrationcampaign (id INTEGER PRIMARY KEY, slug TEXT);
        CREATE TABLE courses_courseregistration (
            id INTEGER PRIMARY KEY, email TEXT, email_normalized TEXT,
            accepted_newsletter BOOL, email_confirmed BOOL, created_at TEXT, campaign_id INT);
        CREATE TABLE data_datamailercontactevent (
            id INTEGER PRIMARY KEY, email TEXT, event_type TEXT, occurred_at TEXT, audience TEXT);
        CREATE TABLE accounts_customuser (id INTEGER PRIMARY KEY, email TEXT);
        INSERT INTO courses_registrationcampaign VALUES (1, 'de-zoomcamp');
        """
    )
    connection.executemany(
        "INSERT INTO courses_courseregistration "
        "(email, email_normalized, accepted_newsletter, email_confirmed, created_at, campaign_id) "
        "VALUES (?, ?, ?, ?, ?, 1)",
        [(e, e.lower(), a, c, at) for e, a, c, at in registrations],
    )
    connection.executemany(
        "INSERT INTO data_datamailercontactevent (email, event_type, occurred_at, audience) "
        "VALUES (?, ?, ?, ?)",
        events or [],
    )
    connection.commit()
    connection.close()


def _csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def _mailchimp(directory: Path, subscribed=(), unsubscribed=(), cleaned=()) -> None:
    directory.mkdir()
    _csv(
        directory / "subscribed_email_audience_export_abc123.csv",
        ["Email Address", "OPTIN_TIME", "CONFIRM_TIME", "LAST_CHANGED"],
        [
            [e, "2021-01-01 00:00:00", "2021-01-01 00:00:00", "2021-01-01 00:00:00"]
            for e in subscribed
        ],
    )
    _csv(
        directory / "unsubscribed_email_audience_export_abc123.csv",
        ["Email Address", "OPTIN_TIME", "CONFIRM_TIME", "UNSUB_TIME"],
        [[e, "2021-01-01 00:00:00", "", "2026-03-01 00:00:00"] for e in unsubscribed],
    )
    _csv(
        directory / "cleaned_email_audience_export_abc123.csv",
        ["Email Address", "OPTIN_TIME", "CONFIRM_TIME", "CLEAN_TIME"],
        [[e, "", "", "2026-03-01 00:00:00"] for e in cleaned],
    )


RELAY_HEADER = [
    "email",
    "audience",
    "client",
    "subscription_status",
    "subscription_created_at",
    "subscription_verified_at",
    "unsubscribed_at",
    "global_unsubscribed_at",
    "hard_bounced_at",
    "complained_at",
]


class DecideTests(SimpleTestCase):
    def test_an_opt_out_anywhere_wins_over_consent_everywhere_else(self) -> None:
        verdict = merge.decide(
            [
                merge.Evidence("cmp", merge.CONSENT, T1),
                merge.Evidence("mailchimp", merge.UNSUBSCRIBED, T0),
            ]
        )
        self.assertEqual(verdict.status, "unsubscribed")
        self.assertTrue(verdict.conflict)
        self.assertTrue(verdict.reconsent_after_opt_out)

    def test_a_bounce_or_complaint_outranks_an_unsubscribe(self) -> None:
        for kind in (merge.BOUNCED, merge.COMPLAINED):
            verdict = merge.decide(
                [merge.Evidence("relay", kind, T0), merge.Evidence("cmp", merge.UNSUBSCRIBED, T0)]
            )
            self.assertEqual(verdict.status, "suppressed")

    def test_neutral_evidence_is_never_consent(self) -> None:
        for kind in merge.NEUTRAL_KINDS:
            self.assertEqual(merge.decide([merge.Evidence("cmp", kind, T0)]).status, "no_evidence")

    def test_an_unsubscribed_row_never_asks_relay_to_resubscribe(self) -> None:
        verdict = merge.decide(
            [merge.Evidence("cmp", merge.CONSENT, T1), merge.Evidence("relay", merge.BOUNCED, T0)]
        )
        row = merge.relay_import_row("a@example.invalid", verdict)
        assert row is not None
        self.assertEqual(row["subscription_status"], "unsubscribed")
        self.assertEqual(row["unsubscribed"], "true")
        self.assertEqual(row["hard_bounced"], "true")

    def test_short_utc_offsets_and_naive_values_parse_as_utc(self) -> None:
        self.assertEqual(
            merge.parse_timestamp("2026-08-25 05:56:48+00"),
            datetime(2026, 8, 25, 5, 56, 48, tzinfo=UTC),
        )
        self.assertEqual(
            merge.parse_timestamp("2021-01-01 00:00:00"), datetime(2021, 1, 1, tzinfo=UTC)
        )
        self.assertIsNone(merge.parse_timestamp(""))

    def test_it_is_a_typed_dry_run_first_operator_command(self) -> None:
        self.assertEqual(merge.SYNC_MODEL, "one-time")
        self.assertFalse(merge.BOOTSTRAPS_EMPTY_DATABASE)
        self.assertIn("import_email_audience_merge", scripts.prod.SCRIPT_COMMAND_EXCLUSIONS)


class MergeRunTests(TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.cmp = self.tmp / "cmp.db"
        _cmp_export(
            self.cmp,
            [
                ("both@example.invalid", 1, 1, "2026-08-25 05:56:48.630321+00"),
                ("cmponly@example.invalid", 1, 1, "2026-08-25 05:56:48+00"),
                ("nobox@example.invalid", 0, 1, "2026-08-25 05:56:48+00"),
                ("unconfirmed@example.invalid", 1, 0, "2026-08-25 05:56:48+00"),
                ("reconsent@example.invalid", 1, 1, "2026-09-01 00:00:00+00"),
            ],
            [
                (
                    "cmpbounce@example.invalid",
                    "contact.hard_bounced",
                    "2026-07-01 00:00:00+00",
                    "dtc-courses",
                ),
                (
                    "load@example.invalid",
                    "contact.hard_bounced",
                    "2026-07-01 00:00:00+00",
                    "relay240-loadtest",
                ),
            ],
        )
        self.mailchimp = self.tmp / "mailchimp"
        _mailchimp(
            self.mailchimp,
            subscribed=["mconly@example.invalid", "cmpbounce@example.invalid"],
            unsubscribed=["reconsent@example.invalid"],
            cleaned=["cleaned@example.invalid"],
        )
        self.relay = self.tmp / "relay"
        self.relay.mkdir()
        _csv(
            self.relay / merge.RELAY_CONTACTS_FILE,
            RELAY_HEADER,
            [
                [
                    "both@example.invalid",
                    "dtc",
                    "dtc-newsletter",
                    "subscribed",
                    "2026-01-01T00:00:00+00:00",
                    "",
                    "",
                    "",
                    "",
                    "",
                ],
                [
                    "relayonly@example.invalid",
                    "dtc",
                    "dtc-newsletter",
                    "subscribed",
                    "2026-01-01T00:00:00+00:00",
                    "",
                    "",
                    "",
                    "",
                    "",
                ],
                [
                    "pending@example.invalid",
                    "dtc",
                    "dtc-newsletter",
                    "pending",
                    "2026-01-01T00:00:00+00:00",
                    "",
                    "",
                    "",
                    "",
                    "",
                ],
                [
                    "complaint@example.invalid",
                    "dtc",
                    "dtc-newsletter",
                    "subscribed",
                    "2026-01-01T00:00:00+00:00",
                    "",
                    "",
                    "",
                    "",
                    "2026-05-01T00:00:00+00:00",
                ],
            ],
        )
        user_model = get_user_model()
        self.cmponly = user_model.objects.create(
            username="cmponly", email="CmpOnly@example.invalid", newsletter_subscribed=False
        )
        # Accounts created before the default turned false (accounts 0012)
        # hold true without any consent; the merge is what corrects them.
        self.nobox = user_model.objects.create(
            username="nobox", email="nobox@example.invalid", newsletter_subscribed=True
        )
        self.decided = user_model.objects.create(
            username="decided", email="sitedecided@example.invalid", newsletter_subscribed=True
        )
        user_model.objects.filter(pk=self.decided.pk).update(newsletter_preference_changed_at=T1)
        self.reconsent = user_model.objects.create(
            username="reconsent", email="reconsent@example.invalid", newsletter_subscribed=True
        )
        user_model.objects.filter(pk=self.reconsent.pk).update(newsletter_preference_changed_at=T1)

    def _run(self, *, apply: bool, name: str) -> dict:
        return merge.run(
            cmp_export=self.cmp,
            mailchimp_export_dir=self.mailchimp,
            relay_export_dir=self.relay,
            output_dir=self.tmp / name,
            apply=apply,
        )

    def test_buckets_follow_the_rules(self) -> None:
        report = self._run(apply=False, name="dry")
        buckets = report["buckets"]
        self.assertEqual(buckets["both_cmp_and_relay_subscribed"], 1)
        self.assertEqual(buckets["cmp_only"], 1)
        self.assertEqual(buckets["relay_only"], 1)
        self.assertEqual(buckets["mailchimp_only"], 1)
        self.assertEqual(buckets["site_only"], 1)
        # reconsent: Mailchimp unsubscribe beats the later CMP registration.
        self.assertEqual(buckets["unsubscribed_somewhere"], 1)
        # cleaned (Mailchimp), cmpbounce (CMP), complaint (Relay); the load test is ignored.
        self.assertEqual(buckets["bounced_or_complained"], 3)
        # reconsent and cmpbounce had consent in a source other than the opt-out's.
        self.assertEqual(buckets["conflicting"], 2)
        self.assertEqual(report["conflicts"]["consent_newer_than_latest_opt_out"], 1)
        # nobox, unconfirmed, pending.
        self.assertEqual(buckets["no_consent_evidence"], 3)

    def test_dry_run_writes_no_account_and_apply_is_idempotent(self) -> None:
        dry = self._run(apply=False, name="dry")
        self.cmponly.refresh_from_db()
        self.assertFalse(self.cmponly.newsletter_subscribed)
        self.assertEqual(dry["site_accounts"]["to_true"], 1)

        first = self._run(apply=True, name="apply-1")
        self.assertEqual(first["site_accounts"]["to_true"], 1)
        user_model = get_user_model()
        values = dict(user_model.objects.values_list("username", "newsletter_subscribed"))
        self.assertTrue(values["cmponly"])
        self.assertFalse(values["nobox"])  # registered without the box: never added
        self.assertTrue(values["decided"])  # a member's own decision is kept...
        self.assertFalse(values["reconsent"])  # ...unless an opt-out exists somewhere
        self.assertIsNone(
            user_model.objects.get(username="cmponly").newsletter_preference_changed_at
        )

        second = self._run(apply=True, name="apply-2")
        self.assertEqual(second["site_accounts"]["to_true"], 0)
        self.assertEqual(second["site_accounts"]["to_false"], 0)
        self.assertEqual(user_model.objects.count(), 4)  # never creates an account

    def test_outputs_are_private_and_the_report_holds_no_address(self) -> None:
        self._run(apply=False, name="out")
        out = self.tmp / "out"
        self.assertEqual(out.stat().st_mode & 0o777, 0o700)
        for name in (
            "report.json",
            "relay-import-dtc.csv",
            "evidence.jsonl",
            "site-account-changes.json",
        ):
            self.assertEqual((out / name).stat().st_mode & 0o777, 0o600)
        self.assertNotIn("@", (out / "report.json").read_text())
        rows = list(csv.DictReader((out / "relay-import-dtc.csv").open()))
        self.assertEqual(
            {r["email"] for r in rows if r["subscription_status"] == "subscribed"},
            {
                "both@example.invalid",
                "cmponly@example.invalid",
                "relayonly@example.invalid",
                "mconly@example.invalid",
                "sitedecided@example.invalid",  # a member's own stamped "yes"
            },
        )
        changes = json.loads((out / "site-account-changes.json").read_text())
        self.assertEqual(changes["to_true"], [self.cmponly.pk])

    def test_output_inside_the_repository_is_refused(self) -> None:
        with self.assertRaises(merge.AudienceMergeError):
            merge.run(
                cmp_export=self.cmp,
                mailchimp_export_dir=None,
                relay_export_dir=None,
                output_dir=merge.PROJECT_ROOT / ".tmp" / "audience-test",
                apply=False,
            )
