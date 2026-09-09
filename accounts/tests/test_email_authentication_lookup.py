"""Email authentication matches the indexed normalized key, bounded (BE-09).

Every sign-in attempt used to normalize and compare every eligible account in
Python.  The lookup now runs in the database against the indexed
``normalized_email`` column with a bounded result set, and the Python-side
comparison is constrained to the rows that have no normalized key yet.
"""

from __future__ import annotations

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from accounts.backends import DurableAccountBackend
from accounts.models import CustomUser

QUARANTINED = CustomUser.IdentityState.QUARANTINED


def make_user(email: str, *, username: str | None = None, **fields) -> CustomUser:
    return CustomUser.objects.create_user(
        username=username or email.split("@", 1)[0].replace(".", "-").replace("@", "-"),
        email=email,
        **fields,
    )


def account_selects(context) -> list[dict[str, str]]:
    return [
        query
        for query in context.captured_queries
        if query["sql"].lstrip().upper().startswith("SELECT")
    ]


class EmailCandidateLookupTests(TestCase):
    def setUp(self) -> None:
        self.backend = DurableAccountBackend()

    def test_case_normalized_login_resolves_through_the_index(self) -> None:
        user = make_user("Learner@Example.com")

        with CaptureQueriesContext(connection) as context:
            candidates = self.backend._email_candidates("learner@example.com")

        self.assertEqual([candidate.pk for candidate in candidates], [user.pk])
        selects = account_selects(context)
        # The indexed lookup alone answers; the fallback never runs.
        self.assertEqual(len(selects), 1)
        self.assertIn("normalized_email", selects[0]["sql"])
        self.assertIn("LIMIT", selects[0]["sql"].upper())

    def test_unknown_email_is_one_bounded_index_lookup(self) -> None:
        make_user("someone@example.com")
        make_user("someone-else@example.com")

        with CaptureQueriesContext(connection) as context:
            candidates = self.backend._email_candidates("nobody@example.invalid")

        self.assertEqual(candidates, ())
        # The indexed lookup misses, so the constrained blank-key fallback
        # runs too -- but every account query keeps the normalized_email
        # predicate, so neither can be a full-table scan.
        selects = account_selects(context)
        self.assertEqual(len(selects), 2)
        for query in selects:
            self.assertIn("normalized_email", query["sql"])

    def test_duplicate_addresses_stay_ambiguous(self) -> None:
        # The partial unique constraint keeps distinct normalized keys unique
        # among active accounts, so same-address duplicates can only exist in
        # the not-yet-normalized population the fallback scans.
        first = make_user("Twin@Example.invalid", username="twin-one")
        second = make_user("twin@example.invalid", username="twin-two")
        CustomUser.objects.filter(pk__in=(first.pk, second.pk)).update(normalized_email="")

        candidates = self.backend._email_candidates("TWIN@example.invalid")

        self.assertEqual(len(candidates), 2)
        self.assertIsNone(
            self.backend._authenticate(
                request=None, email="twin@example.invalid", password="irrelevant"
            )
        )

    def test_inactive_and_quarantined_accounts_are_ineligible(self) -> None:
        make_user("live@example.com")
        make_user("off@example.invalid", username="off", is_active=False)
        quarantined = make_user("q@example.invalid", username="q")
        CustomUser.objects.filter(pk=quarantined.pk).update(identity_state=QUARANTINED)

        self.assertEqual(self.backend._email_candidates("q@example.invalid"), ())
        self.assertEqual(self.backend._email_candidates("off@example.invalid"), ())
        self.assertEqual(len(self.backend._email_candidates("live@example.com")), 1)

    def test_blank_normalized_rows_are_the_only_python_compared_population(self) -> None:
        make_user("backfilled@example.com")
        fallback_user = make_user("Fallback@Example.invalid", username="fallback-row")
        CustomUser.objects.filter(pk=fallback_user.pk).update(normalized_email="")
        unrelated_blank = make_user("other@example.invalid", username="other-blank")
        CustomUser.objects.filter(pk=unrelated_blank.pk).update(normalized_email="")

        with CaptureQueriesContext(connection) as context:
            candidates = self.backend._email_candidates("fallback@example.invalid")

        self.assertEqual([candidate.pk for candidate in candidates], [fallback_user.pk])
        # Two queries: the indexed column, then the blank-key population. No
        # account query is allowed to skip the normalized_email predicate --
        # that is what would make it a full-table scan (audit BE-09).
        selects = account_selects(context)
        self.assertEqual(len(selects), 2)
        for query in selects:
            self.assertIn("normalized_email", query["sql"])

    def test_a_backfilled_population_never_scans_all_accounts(self) -> None:
        for index in range(30):
            make_user(f"member-{index}@example.com", username=f"member-{index}")

        with CaptureQueriesContext(connection) as context:
            candidates = self.backend._email_candidates("member-7@example.com")

        self.assertEqual(len(candidates), 1)
        for query in account_selects(context):
            self.assertIn("normalized_email", query["sql"])
