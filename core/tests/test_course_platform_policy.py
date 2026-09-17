from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts_ext.models import IdentityState, set_identity_state
from website.loginas_policy import can_login_as


def _actor(*, is_staff, is_authenticated=True, is_active=True):
    return SimpleNamespace(
        is_staff=is_staff,
        is_authenticated=is_authenticated,
        is_active=is_active,
    )


class CopiedLoginAsPolicyTests(TestCase):
    """The policy denies every management or identity boundary crossing.

    The impersonation target is a real account: its identity state lives on
    ``accounts_ext.IdentityState`` (plan D3.1), so a stand-in object with an
    ``identity_state`` attribute would no longer be read by the policy and the
    quarantine denial below would pass without testing anything.
    """

    counter = 0

    def _target(self, *, is_staff=False, is_active=True, identity_state=None):
        type(self).counter += 1
        user = get_user_model().objects.create_user(
            username=f"loginas-target-{self.counter}",
            email=f"loginas-target-{self.counter}@example.com",
            password="testpass123",
            is_staff=is_staff,
            is_active=is_active,
        )
        if identity_state is not None:
            set_identity_state(user, identity_state)
        user.refresh_from_db()
        return user

    def test_staff_can_impersonate_only_nonstaff_users(self) -> None:
        staff_request = SimpleNamespace(user=_actor(is_staff=True))
        regular_request = SimpleNamespace(user=_actor(is_staff=False))
        regular_target = self._target(identity_state=IdentityState.States.ACTIVE)
        staff_target = self._target(is_staff=True)

        self.assertIs(can_login_as(staff_request, regular_target), True)
        self.assertIs(can_login_as(staff_request, staff_target), False)
        self.assertIs(can_login_as(regular_request, regular_target), False)

    def test_inactive_or_unauthenticated_staff_cannot_impersonate(self) -> None:
        request = SimpleNamespace(
            user=_actor(is_staff=True, is_authenticated=False),
        )
        self.assertIs(can_login_as(request, self._target()), False)

        request = SimpleNamespace(user=_actor(is_staff=True, is_active=False))
        self.assertIs(can_login_as(request, self._target()), False)

    def test_inactive_and_quarantined_targets_are_denied(self) -> None:
        staff_request = SimpleNamespace(user=_actor(is_staff=True))

        self.assertIs(
            can_login_as(staff_request, self._target(is_active=False)),
            False,
        )
        quarantined = self._target(identity_state=IdentityState.States.QUARANTINED)
        self.assertEqual(
            IdentityState.objects.get(user=quarantined).identity_state,
            IdentityState.States.QUARANTINED,
        )
        self.assertIs(can_login_as(staff_request, quarantined), False)

    def test_an_account_with_no_identity_row_is_treated_as_legacy(self) -> None:
        # Bulk-created accounts carry no identity row; they read as the old
        # column default, which is not quarantined and so is impersonable.
        staff_request = SimpleNamespace(user=_actor(is_staff=True))
        target = self._target()
        IdentityState.objects.filter(user=target).delete()
        target.refresh_from_db()

        self.assertIs(can_login_as(staff_request, target), True)
