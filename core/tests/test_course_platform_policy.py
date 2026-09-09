from types import SimpleNamespace

from django.test import SimpleTestCase

from website.loginas_policy import can_login_as


def _identity_states():
    return SimpleNamespace(QUARANTINED="quarantined")


def _actor(*, is_staff, is_authenticated=True, is_active=True):
    return SimpleNamespace(
        is_staff=is_staff,
        is_authenticated=is_authenticated,
        is_active=is_active,
    )


def _target(*, is_staff=False, is_active=True, identity_state="active"):
    return SimpleNamespace(
        is_staff=is_staff,
        is_superuser=False,
        is_active=is_active,
        identity_state=identity_state,
        IdentityState=_identity_states(),
    )


class CopiedLoginAsPolicyTests(SimpleTestCase):
    """The policy denies every management or identity boundary crossing."""

    def test_staff_can_impersonate_only_nonstaff_users(self) -> None:
        staff_request = SimpleNamespace(user=_actor(is_staff=True))
        regular_request = SimpleNamespace(user=_actor(is_staff=False))
        regular_target = _target()
        staff_target = _target(is_staff=True)

        self.assertIs(can_login_as(staff_request, regular_target), True)
        self.assertIs(can_login_as(staff_request, staff_target), False)
        self.assertIs(can_login_as(regular_request, regular_target), False)

    def test_inactive_or_unauthenticated_staff_cannot_impersonate(self) -> None:
        request = SimpleNamespace(
            user=_actor(is_staff=True, is_authenticated=False),
        )
        self.assertIs(can_login_as(request, _target()), False)

        request = SimpleNamespace(user=_actor(is_staff=True, is_active=False))
        self.assertIs(can_login_as(request, _target()), False)

    def test_inactive_and_quarantined_targets_are_denied(self) -> None:
        staff_request = SimpleNamespace(user=_actor(is_staff=True))

        self.assertIs(
            can_login_as(staff_request, _target(is_active=False)),
            False,
        )
        self.assertIs(
            can_login_as(
                staff_request,
                _target(identity_state="quarantined"),
            ),
            False,
        )
