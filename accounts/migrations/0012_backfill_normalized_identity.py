from collections import defaultdict

from django.db import migrations, models
from django.db.models import Q


def _normalize(value):
    if not isinstance(value, str):
        return None
    normalized = value.strip().casefold()
    return normalized or None


def backfill_normalized_identity(apps, schema_editor):
    User = apps.get_model("accounts", "CustomUser")
    EmailAddress = apps.get_model("account", "EmailAddress")
    database = schema_editor.connection.alias

    verified_by_user = defaultdict(set)
    users_by_verified_email = defaultdict(set)
    verified_rows = EmailAddress.objects.using(database).filter(verified=True)
    for user_id, email in verified_rows.values_list("user_id", "email"):
        normalized = _normalize(email)
        if normalized is None:
            continue
        verified_by_user[user_id].add(normalized)
        users_by_verified_email[normalized].add(user_id)

    for user in User.objects.using(database).order_by("pk").iterator():
        normalized = _normalize(user.email)
        state = "legacy"
        if normalized in verified_by_user.get(user.pk, set()):
            owners = users_by_verified_email[normalized]
            state = "active" if owners == {user.pk} else "quarantined"
        User.objects.using(database).filter(pk=user.pk).update(
            normalized_email=normalized,
            identity_state=state,
        )


def clear_expanded_identity(apps, schema_editor):
    User = apps.get_model("accounts", "CustomUser")
    database = schema_editor.connection.alias
    User.objects.using(database).update(
        normalized_email=None,
        identity_state="legacy",
    )


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0011_identity_expansion"),
    ]

    operations = [
        migrations.RunPython(
            backfill_normalized_identity,
            clear_expanded_identity,
        ),
        migrations.AddConstraint(
            model_name="customuser",
            constraint=models.UniqueConstraint(
                fields=("normalized_email",),
                condition=(
                    Q(identity_state="active")
                    & Q(normalized_email__isnull=False)
                ),
                name="accounts_active_normalized_email_unique",
            ),
        ),
    ]
