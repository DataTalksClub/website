"""Close the expand window for the two identity fields (plan D3.1c).

``0002_identity_state_data`` copied one row per user at D3.1a migrate time.
``accounts_ext.signals`` has kept ``normalized_email`` in step since, on the
save paths that persist an email write -- but nothing at all has kept
``identity_state`` in step. Every writer of that column in the window is a
queryset update that no ``post_save`` receiver sees, including
``accounts/auth.py::_activate_verified_identity``, which runs on every first
verified social sign-in. Rows created in the window have no identity row at
all.

This is the reader switch, so from here ``identity_state_of(user)`` is what
every reader consults. Without this refresh an account absorbed or quarantined
during the window reads back as ``legacy``, ``identity_state_eligible()``
treats it as eligible, the middleware ABSORBED redirect never fires and the
account signs in again on its own id, and ``can_login_as`` stops refusing a
quarantined account. That is an authorization failure, not stale data, which
is why decision D41 puts the refresh in this pull request rather than
anywhere later.

It creates as well as updates: an update-only refresh passes on every account
that existed at expand time and leaves exactly the accounts created in the
window -- the ones with no row -- reading the ``legacy`` default.

Reverse: the back-copy. It writes the identity values onto the user-table
columns, which still exist until the accounts contract migration (D3.1d) runs,
and leaves the identity rows in place. That is step two of P7's three-step
rollback, in the same shape as the courses refresh.
"""

from django.db import migrations

BATCH_SIZE = 1000

IDENTITY_FIELDS = ("normalized_email", "identity_state")


def _batched(values, size):
    batch = []
    for value in values:
        batch.append(value)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def refresh_identity_state(apps, schema_editor):
    """Create the missing identity rows and update the ones that differ."""

    User = apps.get_model("accounts", "User")
    IdentityState = apps.get_model("accounts_ext", "IdentityState")

    user_ids = User.objects.order_by("pk").values_list("pk", flat=True).iterator()
    for chunk in _batched(user_ids, BATCH_SIZE):
        user_values = {
            row[0]: row[1:]
            for row in User.objects.filter(pk__in=chunk).values_list(
                "pk", *IDENTITY_FIELDS
            )
        }
        rows = {
            row.user_id: row for row in IdentityState.objects.filter(user_id__in=chunk)
        }
        created = []
        updated = []
        for user_id, values in user_values.items():
            row = rows.get(user_id)
            if row is None:
                created.append(
                    IdentityState(user_id=user_id, **dict(zip(IDENTITY_FIELDS, values)))
                )
                continue
            if all(
                getattr(row, field) == value
                for field, value in zip(IDENTITY_FIELDS, values)
            ):
                continue
            for field, value in zip(IDENTITY_FIELDS, values):
                setattr(row, field, value)
            updated.append(row)
        if created:
            IdentityState.objects.bulk_create(created, batch_size=BATCH_SIZE)
        if updated:
            IdentityState.objects.bulk_update(
                updated, IDENTITY_FIELDS, batch_size=BATCH_SIZE
            )


def back_copy_user_identity_columns(apps, schema_editor):
    """Write the identity values back onto the user-table columns.

    Only the rows that differ are written, so a reverse on an untouched
    database is a no-op. A user without an identity row keeps whatever its
    columns hold: there is nothing to copy back, and its columns were never
    superseded.
    """

    User = apps.get_model("accounts", "User")
    IdentityState = apps.get_model("accounts_ext", "IdentityState")

    row_ids = (
        IdentityState.objects.order_by("user_id").values_list("user_id", flat=True).iterator()
    )
    for chunk in _batched(row_ids, BATCH_SIZE):
        row_values = {
            row[0]: row[1:]
            for row in IdentityState.objects.filter(user_id__in=chunk).values_list(
                "user_id", *IDENTITY_FIELDS
            )
        }
        restored = []
        for user in User.objects.filter(pk__in=chunk).iterator():
            values = row_values.get(user.pk)
            if values is None:
                continue
            if all(
                getattr(user, field) == value
                for field, value in zip(IDENTITY_FIELDS, values)
            ):
                continue
            for field, value in zip(IDENTITY_FIELDS, values):
                setattr(user, field, value)
            restored.append(user)
        if restored:
            User.objects.bulk_update(
                restored, IDENTITY_FIELDS, batch_size=BATCH_SIZE
            )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts_ext", "0002_identity_state_data"),
    ]

    operations = [
        migrations.RunPython(refresh_identity_state, back_copy_user_identity_columns),
    ]
