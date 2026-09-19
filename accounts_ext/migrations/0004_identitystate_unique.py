"""Enforce the moved conditional unique constraint on ``IdentityState``.

The constraint keeps its original name, ``accounts_active_normalized_email_unique``
-- it moves, it is not renamed. It could not be created in ``0001_initial``
because the identically named partial index still existed on the user table
(accounts.0001), and an index name exists once per database. The accounts
contract migration removes it there; this migration recreates it, unchanged,
over the identity rows.

Reverses cleanly: the index is dropped, the table and its rows stay.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts_ext", "0003_identity_state_refresh"),
        ("accounts", "0008_contract_moved_fields"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="identitystate",
            constraint=models.UniqueConstraint(
                condition=models.Q(("identity_state", "active"), ("normalized_email__isnull", False)),
                fields=("normalized_email",),
                name="accounts_active_normalized_email_unique",
            ),
        ),
    ]
