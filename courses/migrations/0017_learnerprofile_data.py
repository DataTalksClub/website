"""Copy the course-platform values off every user row (plan D3.1, expand).

One ``LearnerProfile`` row per user, values copied verbatim: nulls stay null
and defaults are never applied to existing rows. Re-runs are guarded --
users that already carry a profile row are skipped -- so ``migrate`` stays
idempotent on databases where rows appeared between code deploy and migrate.

Reverse: the profile values are written back onto the user-table columns,
which still exist until the ``accounts`` contract migration is reversed.
Like every data copy, a reverse after newer profile writes would resurrect
older user-column values; the contract migration's docstring records that
caveat.
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


def copy_learner_profiles(apps, schema_editor):
    LearnerProfile = apps.get_model("courses", "LearnerProfile")
    CustomUser = apps.get_model("accounts", "CustomUser")

    existing_user_ids = set(
        LearnerProfile.objects.values_list("user_id", flat=True).iterator()
    )
    profiles = []
    for user in CustomUser.objects.iterator():
        if user.pk in existing_user_ids:
            continue
        profiles.append(
            LearnerProfile(
                user_id=user.pk,
                **{field: getattr(user, field) for field in PROFILE_FIELDS},
            )
        )
        if len(profiles) >= BATCH_SIZE:
            LearnerProfile.objects.bulk_create(profiles, batch_size=BATCH_SIZE)
            profiles = []
    if profiles:
        LearnerProfile.objects.bulk_create(profiles, batch_size=BATCH_SIZE)


def restore_user_profile_columns(apps, schema_editor):
    LearnerProfile = apps.get_model("courses", "LearnerProfile")
    CustomUser = apps.get_model("accounts", "CustomUser")

    profiles_by_user_id = {
        profile.user_id: profile for profile in LearnerProfile.objects.iterator()
    }
    restored = []
    for user in CustomUser.objects.filter(pk__in=profiles_by_user_id).iterator():
        profile = profiles_by_user_id[user.pk]
        for field in PROFILE_FIELDS:
            setattr(user, field, getattr(profile, field))
        restored.append(user)
        if len(restored) >= BATCH_SIZE:
            CustomUser.objects.bulk_update(restored, PROFILE_FIELDS, batch_size=BATCH_SIZE)
            restored = []
    if restored:
        CustomUser.objects.bulk_update(restored, PROFILE_FIELDS, batch_size=BATCH_SIZE)


class Migration(migrations.Migration):

    dependencies = [
        ("courses", "0016_learnerprofile"),
    ]

    operations = [
        migrations.RunPython(copy_learner_profiles, restore_user_profile_columns),
    ]
