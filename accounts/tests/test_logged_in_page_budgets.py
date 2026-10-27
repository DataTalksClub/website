"""Query budgets for the logged-in pages users hit after login (fast-pages).

Member home and the account pages are per-user surfaces: no credentialed
response may be cached, so these budgets pin the query shape instead --
bounded single-row and small-table reads with no per-item fan-out, the same
way the public budgets do.  Wall-clock time is measured from production logs
and curl (see the intake report), not asserted here.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

#: Warm budgets for the logged-in account surfaces.  Measured on the reviewed
#: fixture catalogue with headroom for small bounded additions; any per-row
#: fan-out would exceed these by the size of the collection, not by one.
#: Member home (the authenticated ``/`` branch) is pinned separately by
#: ``content.tests.test_public_page_query_budgets.MemberHomeQueryBudgetTests``.
WARM_BUDGETS = {
    "/accounts/settings/": 15,
    "/accounts/welcome/": 12,
}


class LoggedInPageQueryBudgetTests(TestCase):
    def _login(self) -> None:
        member = get_user_model().objects.create_user(
            username="budget-logged-in@example.invalid",
            email="budget-logged-in@example.invalid",
        )
        self.client.force_login(member)

    def test_logged_in_pages_render_within_their_warm_query_budget(self) -> None:
        self._login()
        for path, budget in WARM_BUDGETS.items():
            with self.subTest(page=path):
                first = self.client.get(path)
                self.assertEqual(first.status_code, 200, path)
                with CaptureQueriesContext(connection) as captured:
                    response = self.client.get(path)
                self.assertEqual(response.status_code, 200, path)
                self.assertLessEqual(
                    len(captured),
                    budget,
                    f"{path} spent {len(captured)} queries warm (budget {budget})",
                )
