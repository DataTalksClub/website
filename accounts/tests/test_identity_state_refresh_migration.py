"""The refresh that closes the D3.1a expand window (accounts_ext.0003).

``accounts_ext.signals`` keeps ``normalized_email`` in step on the save paths
that persist an email write. Nothing keeps ``identity_state`` in step on any
path at all: every writer of that column during the window is a queryset
update, and a ``post_save`` receiver does not see one.
``accounts.auth._activate_verified_identity`` is the live example -- a
compare-and-swap ``update()`` that runs on every first verified social sign-in.

From this deploy ``identity_state_of(user)`` is what every reader consults, so
a row still saying ``legacy`` for an account absorbed or quarantined in the
window is not stale data. ``identity_state_eligible()`` returns true, the
middleware ABSORBED redirect never fires and the account signs in again on its
own id, and ``can_login_as`` stops refusing a quarantined account. These tests
simulate the window with the writer shape that caused it -- a queryset update
on the user column, which is what the pre-switch code ran -- and pin both that
authorization consequence and the create case an update-only refresh misses.

They run against a private database the test migrates itself, through the
historical models of each node: the contract phase drops the user columns, and
from then on no database in the suite has both ends of the copy. Migrating from
the schema node to the expand-copy node also exercises the copy itself, which
is the other half of the split the rollback needs.

Addresses use the RFC 2606 reserved ``.invalid`` domain.
"""

from __future__ import annotations

from types import SimpleNamespace

from accounts.identity_resolution import identity_state_eligible
from accounts_ext.models import IdentityState
from test_support.migrations import MigrationWindowTestCase
from website.loginas_policy import can_login_as

#: The user model's name in migration state, renamed by D3.1e.
USER_MODEL = "User"

#: ``accounts`` is held before its contract migration so the user columns the
#: refresh reads are still in the schema, on this branch and on every later one.
ACCOUNTS_PRE_CONTRACT = ("accounts", "0007_move_identity_models_state")
EXTENSION_SCHEMA = ("accounts_ext", "0001_initial")
EXPAND_COPY = ("accounts_ext", "0002_identity_state_data")
REFRESH = ("accounts_ext", "0003_identity_state_refresh")

#: ``can_login_as`` reads exactly these three attributes off ``request.user``;
#: a migration-state model is not an ``AbstractUser`` and has none of them.
STAFF_VIEWER = SimpleNamespace(is_authenticated=True, is_active=True, is_staff=True)


class IdentityStateRefreshTests(MigrationWindowTestCase):
    def _create_user(self, model, suffix: str, **values):
        return model.objects.create(
            username=f"identity-{suffix}@example.invalid",
            email=f"identity-{suffix}@example.invalid",
            password="!",
            **values,
        )

    def _account_present_at_expand_time(self, suffix: str):
        """An account the expand copy saw: it exists before the copy runs."""

        state = self.migrate(ACCOUNTS_PRE_CONTRACT, EXTENSION_SCHEMA)
        User = state.get_model("accounts", USER_MODEL)
        user = self._create_user(User, suffix)

        state = self.migrate(ACCOUNTS_PRE_CONTRACT, EXPAND_COPY)
        User = state.get_model("accounts", USER_MODEL)
        State = state.get_model("accounts_ext", "IdentityState")
        self.assertEqual(
            State.objects.get(user_id=user.pk).identity_state,
            IdentityState.States.LEGACY,
        )
        return User, State, user.pk

    def test_absorbed_in_the_window_reads_absorbed_and_is_refused(self):
        User, State, user_id = self._account_present_at_expand_time("absorbed")

        # The window writer: a queryset update on the user column, which is
        # what every absorption ran before the readers switched.
        User.objects.filter(pk=user_id).update(identity_state="absorbed")

        # The defect this refresh exists for. The column says absorbed, the row
        # still says legacy, and from this deploy every reader consults the row.
        stale = User.objects.get(pk=user_id)
        self.assertEqual(stale.identity_state, "absorbed")
        self.assertEqual(State.objects.get(user_id=user_id).identity_state, "legacy")
        self.assertTrue(identity_state_eligible(stale))

        state = self.migrate(ACCOUNTS_PRE_CONTRACT, REFRESH)
        User = state.get_model("accounts", USER_MODEL)

        refreshed = User.objects.get(pk=user_id)
        self.assertEqual(
            state.get_model("accounts_ext", "IdentityState")
            .objects.get(user_id=user_id)
            .identity_state,
            IdentityState.States.ABSORBED,
        )
        self.assertFalse(identity_state_eligible(refreshed))

    def test_quarantined_in_the_window_is_refused_impersonation_after_the_refresh(self):
        User, _State, user_id = self._account_present_at_expand_time("quarantined")
        User.objects.filter(pk=user_id).update(identity_state="quarantined")

        request = SimpleNamespace(user=STAFF_VIEWER)
        self.assertTrue(can_login_as(request, User.objects.get(pk=user_id)))

        state = self.migrate(ACCOUNTS_PRE_CONTRACT, REFRESH)
        User = state.get_model("accounts", USER_MODEL)

        self.assertFalse(can_login_as(request, User.objects.get(pk=user_id)))

    def test_account_created_after_the_expand_copy_gets_a_row(self):
        state = self.migrate(ACCOUNTS_PRE_CONTRACT, EXPAND_COPY)
        User = state.get_model("accounts", USER_MODEL)
        State = state.get_model("accounts_ext", "IdentityState")

        # The account arrives after the copy, and the path that created it did
        # not go through the ORM save the receiver listens on, so it has no row.
        user = self._create_user(
            User,
            "created",
            identity_state="quarantined",
            normalized_email="identity-created@example.invalid",
        )
        State.objects.filter(user_id=user.pk).delete()

        # What an update-only refresh would do for this account: nothing.
        self.assertEqual(
            State.objects.filter(user_id=user.pk).update(identity_state="quarantined"),
            0,
            "an update-only refresh is a zero-row no-op for an account created "
            "in the window, which is why the refresh has to create",
        )

        state = self.migrate(ACCOUNTS_PRE_CONTRACT, REFRESH)
        row = state.get_model("accounts_ext", "IdentityState").objects.get(user_id=user.pk)
        self.assertEqual(row.identity_state, IdentityState.States.QUARANTINED)
        self.assertEqual(row.normalized_email, "identity-created@example.invalid")

    def test_reverse_back_copies_the_identity_values_onto_the_user_columns(self):
        state = self.migrate(ACCOUNTS_PRE_CONTRACT, REFRESH)
        User = state.get_model("accounts", USER_MODEL)
        State = state.get_model("accounts_ext", "IdentityState")

        user = self._create_user(User, "reverse")
        # After the reader switch the values land on the row and the columns go
        # stale; reverting the code alone would serve the columns.
        State.objects.create(
            user_id=user.pk,
            identity_state="absorbed",
            normalized_email="survivor@example.invalid",
        )

        state = self.migrate(ACCOUNTS_PRE_CONTRACT, EXPAND_COPY)
        User = state.get_model("accounts", USER_MODEL)
        State = state.get_model("accounts_ext", "IdentityState")

        restored = User.objects.get(pk=user.pk)
        self.assertEqual(restored.identity_state, IdentityState.States.ABSORBED)
        self.assertEqual(restored.normalized_email, "survivor@example.invalid")
        # The expand stays in place: the back-copy restores values onto the
        # columns, it does not unwind the move.
        self.assertEqual(State.objects.filter(user_id=user.pk).count(), 1)
