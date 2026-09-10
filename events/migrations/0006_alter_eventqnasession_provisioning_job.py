"""Point EventQnaSession.provisioning_job at the package JobIntent (D1.1).

The copy migration in the data app preserves durable-job ids, so existing
references stay valid across the swap.

This cannot be a plain AlterField because of where the column comes from:

- ``events.0001`` is already applied on deployed databases, where the
  column carries the legacy foreign key to ``jobs_durablejob`` (that
  table still exists read-only; D1.1 removed only the app).
- A fresh install gets the plain uuid column from ``events.0001``, which
  deliberately carries no FK and no dependency on ``cb_jobs`` -- a
  dependency there would make ``migrate`` refuse to run on every
  deployed database (``InconsistentMigrationHistory``: an applied
  migration may not depend on an unapplied one), which is exactly the
  failure that blocked the 2026-09-10 dev deploy.

So the schema step is introspection-driven and converges both worlds to
the same shape: drop any foreign key on the column that does not target
``cb_jobs_jobintent``, add the package foreign key when absent, and add
the OneToOne unique index when absent. The state half records the final
field so ``makemigrations --check`` stays clean.
"""

from django.db import migrations, models

TABLE = "events_eventqnasession"
COLUMN = "provisioning_job_id"
TARGET_TABLE = "cb_jobs_jobintent"
FK_NAME = "events_eventqna_provisioning_job_intent_fk"
UNIQUE_INDEX = "events_eventqna_provisioning_job_uniq"


def repoint_provisioning_job(apps, schema_editor):
    connection = schema_editor.connection
    quote = connection.ops.quote_name
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, TABLE)

    for name, definition in constraints.items():
        foreign_key = definition.get("foreign_key")
        if not foreign_key or COLUMN not in (definition.get("columns") or []):
            continue
        if foreign_key[0] == TARGET_TABLE:
            continue
        if connection.vendor != "postgresql":
            # SQLite cannot drop a constraint in place and no supported
            # environment carries the legacy key on this vendor; the fresh
            # path below still applies where it matters.
            continue
        schema_editor.execute(f"ALTER TABLE {quote(TABLE)} DROP CONSTRAINT {quote(name)}")

    has_target_fk = any(
        definition.get("foreign_key")
        and definition["foreign_key"][0] == TARGET_TABLE
        and COLUMN in (definition.get("columns") or [])
        for definition in constraints.values()
    )
    if not has_target_fk and connection.vendor == "postgresql":
        schema_editor.execute(
            f"ALTER TABLE {quote(TABLE)} ADD CONSTRAINT {quote(FK_NAME)} "
            f"FOREIGN KEY ({quote(COLUMN)}) "
            f"REFERENCES {quote(TARGET_TABLE)} ({quote('id')}) "
            f"DEFERRABLE INITIALLY DEFERRED"
        )

    has_unique = any(
        definition.get("unique") and definition.get("columns") == [COLUMN]
        for definition in constraints.values()
    )
    if not has_unique:
        schema_editor.execute(
            f"CREATE UNIQUE INDEX {quote(UNIQUE_INDEX)} ON {quote(TABLE)} ({quote(COLUMN)})"
        )


def noop(apps, schema_editor):
    return None


class Migration(migrations.Migration):
    dependencies = [
        ("events", "0005_eventcontent_eventlink_eventspeaker_and_more"),
        ("data", "0003_copy_durable_jobs_to_job_intents"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name="eventqnasession",
                    name="provisioning_job",
                    field=models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=models.SET_NULL,
                        related_name="qna_provisioning_session",
                        to="cb_jobs.jobintent",
                    ),
                ),
            ],
            database_operations=[
                migrations.RunPython(repoint_provisioning_job, noop),
            ],
        ),
    ]
