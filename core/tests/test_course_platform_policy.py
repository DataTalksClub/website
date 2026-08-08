from types import SimpleNamespace

from django.test import SimpleTestCase

from website.loginas_policy import can_login_as


class CopiedLoginAsPolicyTests(SimpleTestCase):
    def test_staff_can_impersonate_only_nonstaff_users(self) -> None:
        staff_request = SimpleNamespace(user=SimpleNamespace(is_staff=True))
        regular_request = SimpleNamespace(user=SimpleNamespace(is_staff=False))
        regular_target = SimpleNamespace(is_staff=False)
        staff_target = SimpleNamespace(is_staff=True)

        self.assertIs(can_login_as(staff_request, regular_target), True)
        self.assertIs(can_login_as(staff_request, staff_target), False)
        self.assertIs(can_login_as(regular_request, regular_target), False)
