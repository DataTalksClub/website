"""Close the expand window for the ten course-platform fields (plan D3.1b).

``0017_learnerprofile_data`` copied one row per user at D3.1a migrate time and
stopped there. From that deploy until this one every write to the ten moved
fields landed on ``accounts_customuser`` and nothing refreshed
``courses_learnerprofile``: rows created in the window have no profile row at
all, and rows edited in the window have a stale one. This migration ships with
the reader switch and re-runs the copy over exactly those rows, which is what
decision D41 and playbook P7 require of the deploy that closes the window.

It creates as well as updates, deliberately. An update-only refresh passes on
every account that existed at expand time and leaves the accounts created in
the window -- the ones with no row -- reading ``profile_field_default(...)``:
empty certificate name, empty country, dark mode off. Those are the accounts a
refresh most needs to reach, so "no row" is a create here, not a skip.

Reverse: the back-copy. It writes the profile values onto the user-table
columns, which still exist until the accounts contract migration (D3.1d) runs,
and leaves the profile rows in place. That is step two of the three-step
rollback in P7 -- revert the code, back-copy, then reverse the migration if it
is being reversed at all -- as a migration rather than a loose script, so the
values written through ``LearnerProfile`` while the switched code was live are
restored onto the columns the reverted code reads.
"""

from django.db import migrations

BATCH_SIZE = 500

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


def _batched(values, size):
    batch = []
    for value in values:
        batch.append(value)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def refresh_learner_profiles(apps, schema_editor):
    """Create the missing profile rows and update the ones that differ."""

    LearnerProfile = apps.get_model("courses", "LearnerProfile")
    CustomUser = apps.get_model("accounts", "CustomUser")

    user_ids = CustomUser.objects.order_by("pk").values_list("pk", flat=True).iterator()
    for chunk in _batched(user_ids, BATCH_SIZE):
        user_values = {
            row[0]: row[1:]
            for row in CustomUser.objects.filter(pk__in=chunk).values_list(
                "pk", *PROFILE_FIELDS
            )
        }
        profiles = {
            profile.user_id: profile
            for profile in LearnerProfile.objects.filter(user_id__in=chunk)
        }
        created = []
        updated = []
        for user_id, values in user_values.items():
            profile = profiles.get(user_id)
            if profile is None:
                created.append(
                    LearnerProfile(user_id=user_id, **dict(zip(PROFILE_FIELDS, values)))
                )
                continue
            if all(
                getattr(profile, field) == value
                for field, value in zip(PROFILE_FIELDS, values)
            ):
                continue
            for field, value in zip(PROFILE_FIELDS, values):
                setattr(profile, field, value)
            updated.append(profile)
        if created:
            LearnerProfile.objects.bulk_create(created, batch_size=BATCH_SIZE)
        if updated:
            LearnerProfile.objects.bulk_update(
                updated, PROFILE_FIELDS, batch_size=BATCH_SIZE
            )


def back_copy_user_profile_columns(apps, schema_editor):
    """Write the profile values back onto the user-table columns.

    Only the rows that differ are written, so a reverse on an untouched
    database is a no-op rather than a full table rewrite. A user without a
    profile row keeps whatever its columns hold: there is nothing to copy
    back, and its columns were never superseded.
    """

    LearnerProfile = apps.get_model("courses", "LearnerProfile")
    CustomUser = apps.get_model("accounts", "CustomUser")

    profile_ids = (
        LearnerProfile.objects.order_by("user_id").values_list("user_id", flat=True).iterator()
    )
    for chunk in _batched(profile_ids, BATCH_SIZE):
        profile_values = {
            row[0]: row[1:]
            for row in LearnerProfile.objects.filter(user_id__in=chunk).values_list(
                "user_id", *PROFILE_FIELDS
            )
        }
        restored = []
        for user in CustomUser.objects.filter(pk__in=chunk).iterator():
            values = profile_values.get(user.pk)
            if values is None:
                continue
            if all(
                getattr(user, field) == value
                for field, value in zip(PROFILE_FIELDS, values)
            ):
                continue
            for field, value in zip(PROFILE_FIELDS, values):
                setattr(user, field, value)
            restored.append(user)
        if restored:
            CustomUser.objects.bulk_update(restored, PROFILE_FIELDS, batch_size=BATCH_SIZE)


class Migration(migrations.Migration):

    dependencies = [
        ("courses", "0017_learnerprofile_data"),
    ]

    operations = [
        migrations.RunPython(refresh_learner_profiles, back_copy_user_profile_columns),
    ]
