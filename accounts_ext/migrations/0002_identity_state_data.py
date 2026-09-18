"""Copy the identity reconciliation values off every user row (plan D3.1, expand).

One ``IdentityState`` row per user, values copied verbatim. Split out of
``0001_initial`` on purpose: the create and the copy are separate migrations
so the copy can be reversed on its own. Reversing this migration writes the
stored values back onto the still-present user-table columns and leaves the
table, the rows and the four state-only model moves in place -- the state a
rollback of the reader switch needs (playbook P7, decision D41). A combined
create-and-copy migration cannot reach it.

Guarded both ways: users that already carry a row are skipped on the way
forward, and users without one are left alone on the way back, so a re-run is
a no-op rather than a rewrite.

This is the expand-time copy only. It is correct for exactly as long as it
takes the next write to land on the user columns, so the reader switch
(D3.1c) ships ``0003_identity_state_refresh``, which re-runs the copy over
the rows created or changed in the window.
"""

from django.db import migrations

BATCH_SIZE = 1000


def copy_identity_state(apps, schema_editor):
    """Copy the reconciliation values off every user row, verbatim.

    Guarded: users that already carry an ``IdentityState`` row are skipped,
    so the copy stays idempotent on databases where rows appeared between the
    code deploy and the migrate run.
    """

    User = apps.get_model("accounts", "User")
    IdentityState = apps.get_model("accounts_ext", "IdentityState")

    existing_user_ids = set(
        IdentityState.objects.values_list("user_id", flat=True).iterator()
    )
    identities = []
    for user in User.objects.iterator():
        if user.pk in existing_user_ids:
            continue
        identities.append(
            IdentityState(
                user_id=user.pk,
                normalized_email=user.normalized_email,
                identity_state=user.identity_state,
            )
        )
        if len(identities) >= BATCH_SIZE:
            IdentityState.objects.bulk_create(identities, batch_size=BATCH_SIZE)
            identities = []
    if identities:
        IdentityState.objects.bulk_create(identities, batch_size=BATCH_SIZE)


def restore_user_identity_columns(apps, schema_editor):
    """Reverse copy: write the reconciliation values back onto the user rows.

    This is the back-copy step of the rollback, as a migration rather than a
    loose script: the user-table columns still exist while it runs (the
    accounts contract migration has not been reversed yet), the identity table
    keeps its rows, and ``0001_initial`` stays applied. Users without a row
    keep whatever their columns hold, mirroring a row-less bulk-created
    account.
    """

    User = apps.get_model("accounts", "User")
    IdentityState = apps.get_model("accounts_ext", "IdentityState")

    identities_by_user_id = {
        user_id: (normalized_email, identity_state)
        for user_id, normalized_email, identity_state in IdentityState.objects.values_list(
            "user_id", "normalized_email", "identity_state"
        )
    }
    restored = []
    for user in User.objects.iterator():
        identity = identities_by_user_id.get(user.pk)
        if identity is None:
            continue
        user.normalized_email, user.identity_state = identity
        restored.append(user)
        if len(restored) >= BATCH_SIZE:
            User.objects.bulk_update(
                restored, ["normalized_email", "identity_state"], batch_size=BATCH_SIZE
            )
            restored = []
    if restored:
        User.objects.bulk_update(
            restored, ["normalized_email", "identity_state"], batch_size=BATCH_SIZE
        )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts_ext", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(copy_identity_state, restore_user_identity_columns),
    ]
