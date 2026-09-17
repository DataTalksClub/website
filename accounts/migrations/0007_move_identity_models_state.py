"""Move the four identity evidence models out of the accounts app state.

Plan issue D3.1a (playbook P7, DTC step 1, expand half). ``AccountIdentityAlias``,
``AccountIdentityQuarantine``, ``AccountReconciliationRun`` and
``CmpLearnerImportProgress`` keep their physical tables exactly where they are;
only their app registration moves to ``accounts_ext``, whose initial migration
declares the same tables state-only with their ``db_table`` pinned.

Both halves of the move are therefore recorded with
``SeparateDatabaseAndState`` and empty database operations: no table is created,
renamed or dropped, and no row is read or written by this migration.

Reverses as an operation: the reverse recreates the four models in the accounts
app state, again state-only.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0006_newsletter_preference_changed_at_mailchimpsubscri"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name="AccountIdentityAlias"),
                migrations.DeleteModel(name="AccountIdentityQuarantine"),
                migrations.DeleteModel(name="AccountReconciliationRun"),
                migrations.DeleteModel(name="CmpLearnerImportProgress"),
            ],
            database_operations=[],
        ),
    ]
