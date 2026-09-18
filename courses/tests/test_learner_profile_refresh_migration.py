"""The refresh that closes the D3.1a expand window (courses.0018).

The expand copied one row per user at D3.1a migrate time and stopped there.
Nothing dual-writes the ten moved fields, so between that deploy and this one
every write landed on the user table alone. Two shapes come out of that window,
and only one of them is obvious:

- an account edited in the window has a profile row holding stale values;
- an account created in the window has no profile row at all.

The second is the one a refresh gets wrong. An update-only refresh passes on
every account that existed at expand time, which is nearly all of them, so it
looks right; the accounts it silently skips are exactly the ones whose values
are about to be read as ``profile_field_default(...)`` and then dropped by the
contract migration. ``test_account_created_after_the_expand_copy_gets_a_row``
pins that case, and asserts the update-only shape is a zero-row no-op first, so
the difference is visible rather than argued.

These run against a private database the test migrates itself, through the
historical models of each node, rather than through the live models: the
contract phase drops the user columns, and from then on no database in the
suite has both ends of the copy. The same test therefore keeps meaning on every
branch of the move.

Addresses and URLs use the RFC 2606 reserved ``.invalid`` domain.
"""

from __future__ import annotations

from test_support.migrations import MigrationWindowTestCase

#: The user model's name in migration state, renamed by D3.1e.
USER_MODEL = "User"

#: ``accounts`` is held before its contract migration so the user columns the
#: refresh reads are still in the schema, on this branch and on every later one.
ACCOUNTS_PRE_CONTRACT = ("accounts", "0007_move_identity_models_state")
EXPAND_COPY = ("courses", "0017_learnerprofile_data")
REFRESH = ("courses", "0018_learnerprofile_refresh")

PROFILE_FIELDS = (
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

WINDOW_VALUES = {
    "role": "instructor",
    "certificate_name": "Window Learner",
    "country": "Germany",
    "region": "Berlin",
    "registration_role": "data engineer",
    "github_url": "https://github.example.invalid/window",
    "linkedin_url": "https://linkedin.example.invalid/window",
    "personal_website_url": "https://window.example.invalid/",
    "about_me": "Signed up while the expand window was open.",
    "dark_mode": True,
}


class LearnerProfileRefreshTests(MigrationWindowTestCase):
    def _create_user(self, model, suffix: str, **values):
        return model.objects.create(
            username=f"window-{suffix}@example.invalid",
            email=f"window-{suffix}@example.invalid",
            password="!",
            **values,
        )

    def _assert_window_values(self, row):
        for field, value in WINDOW_VALUES.items():
            with self.subTest(field=field):
                self.assertEqual(getattr(row, field), value)

    def test_account_created_after_the_expand_copy_gets_a_row(self):
        state = self.migrate(ACCOUNTS_PRE_CONTRACT, EXPAND_COPY)
        User = state.get_model("accounts", USER_MODEL)
        LearnerProfile = state.get_model("courses", "LearnerProfile")

        # The account arrives after the expand copy has run, so it has the
        # values on its columns and no profile row at all.
        user = self._create_user(User, "created", **WINDOW_VALUES)
        self.assertEqual(LearnerProfile.objects.filter(user_id=user.pk).count(), 0)

        # What an update-only refresh would do for this account: nothing.
        self.assertEqual(
            LearnerProfile.objects.filter(user_id=user.pk).update(**WINDOW_VALUES),
            0,
            "an update-only refresh is a zero-row no-op for an account created "
            "in the window, which is why the refresh has to create",
        )

        state = self.migrate(ACCOUNTS_PRE_CONTRACT, REFRESH)
        LearnerProfile = state.get_model("courses", "LearnerProfile")
        self._assert_window_values(LearnerProfile.objects.get(user_id=user.pk))

    def test_account_edited_in_the_window_is_updated_and_a_current_row_is_kept(self):
        state = self.migrate(ACCOUNTS_PRE_CONTRACT, EXPAND_COPY)
        User = state.get_model("accounts", USER_MODEL)
        LearnerProfile = state.get_model("courses", "LearnerProfile")

        edited = self._create_user(User, "edited")
        LearnerProfile.objects.create(user_id=edited.pk)
        # A writer in the window reaches the user column and nothing carries it
        # to the profile row -- a queryset update sees no post_save receiver.
        User.objects.filter(pk=edited.pk).update(**WINDOW_VALUES)

        current = self._create_user(User, "current", **WINDOW_VALUES)
        LearnerProfile.objects.create(user_id=current.pk, **WINDOW_VALUES)

        state = self.migrate(ACCOUNTS_PRE_CONTRACT, REFRESH)
        LearnerProfile = state.get_model("courses", "LearnerProfile")

        self._assert_window_values(LearnerProfile.objects.get(user_id=edited.pk))
        self.assertEqual(LearnerProfile.objects.filter(user_id=current.pk).count(), 1)
        self._assert_window_values(LearnerProfile.objects.get(user_id=current.pk))

    def test_reverse_back_copies_the_profile_values_onto_the_user_columns(self):
        state = self.migrate(ACCOUNTS_PRE_CONTRACT, REFRESH)
        User = state.get_model("accounts", USER_MODEL)
        LearnerProfile = state.get_model("courses", "LearnerProfile")

        # After the reader switch the values land on the profile row and the
        # columns go stale; reverting the code alone would serve the columns.
        user = self._create_user(User, "reverse")
        LearnerProfile.objects.create(user_id=user.pk, **WINDOW_VALUES)

        state = self.migrate(ACCOUNTS_PRE_CONTRACT, EXPAND_COPY)
        User = state.get_model("accounts", USER_MODEL)
        LearnerProfile = state.get_model("courses", "LearnerProfile")

        self._assert_window_values(User.objects.get(pk=user.pk))
        # The expand stays in place: the back-copy restores values onto the
        # columns, it does not unwind the move.
        self.assertEqual(LearnerProfile.objects.filter(user_id=user.pk).count(), 1)
