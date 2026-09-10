"""Drop the tables of the two retired email apps (D1.2cb).

D1.2cb deletes ``email_app`` (the donor ``PendingUnsubscribe`` and the relay
link bridge) and the ``data`` app (the datamailer outbox, send audit, dispatch
run and contact event storage). Both apps are gone from the codebase in the
same change, so this migration speaks raw SQL against the tables they left
behind; their migration records stay in ``django_migrations`` untouched.

Every drop is guarded on the table existing:

- Deployed databases have the tables; the D1.2a copy already moved every
  ``PendingUnsubscribe`` row into the package's ``cb_mail_pendingunsubscribe``
  (the durable unsubscribe keeps working there), and the rollback window for
  the donor table and the read-only datamailer storage closed with the green
  D1.2a/D1.2b development deploys.
- Fresh installs never had these tables and skip everything.

Ordering needs no dependency on the deleted apps: on any database where their
migrations are recorded, they applied long before this one, and the guard makes
every other starting state a no-op.
"""

from django.db import migrations

TABLES = (
    "data_datamailercontactevent",
    "data_datamailersendaudit",
    "data_datamaileroutboxdispatchrun",
    "data_datamaileroutboxevent",
    "email_app_pendingunsubscribe",
)


def drop_retired_tables(apps, schema_editor):
    names = set(
        schema_editor.connection.introspection.table_names(
            schema_editor.connection.cursor()
        )
    )
    for table in TABLES:
        if table in names:
            schema_editor.execute(f"DROP TABLE {table}")


def noop(apps, schema_editor):
    return None


class Migration(migrations.Migration):
    dependencies = [
        # The copy migration is the current tip of core's chain (it follows
        # 0007); hanging off it keeps the graph linear.
        ("core", "0003_copy_operational_settings_to_cb_config"),
    ]

    operations = [
        migrations.RunPython(drop_retired_tables, noop),
    ]
