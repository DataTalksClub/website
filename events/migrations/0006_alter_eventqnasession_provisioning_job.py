"""Point EventQnaSession.provisioning_job at the package JobIntent (D1.1).

The copy migration in the data app preserves durable-job ids, so existing
references stay valid across the swap.

This cannot be a plain AlterField because of where the column comes from:

- ``events.0001`` is already applied on deployed databases, where the
  column carries the legacy foreign key to ``jobs_durablejob`` (that
  table still exists read-only; D1.1 removed only the app).
- A fresh install gets a bare uuid column from ``events.0001``, which
  deliberately carries no foreign key and no dependency on ``cb_jobs`` --
  a dependency there would make ``migrate`` refuse to run on every
  deployed database (``InconsistentMigrationHistory``: an applied
  migration may not depend on an unapplied one), which is exactly the
  failure that blocked the 2026-09-10 dev deploy.

So the schema step keys off database state, not engine, and converges
both worlds to the same shape. A database still carrying the legacy
foreign key is a deployed one: the stale key is dropped and the package
foreign key added in its place. Otherwise the database is fresh: the
bare column is replaced with the full one-to-one column through the
portable schema-editor operations. The state half records the final
field so ``makemigrations --check`` stays clean.
"""

from django.db import migrations, models

TABLE = "events_eventqnasession"
COLUMN = "provisioning_job_id"
TARGET_TABLE = "cb_jobs_jobintent"


def _field_constraints(connection, table):
    with connection.cursor() as cursor:
        return connection.introspection.get_constraints(cursor, table)


def _foreign_keys(constraints):
    found = {}
    for name, definition in constraints.items():
        foreign_key = definition.get("foreign_key")
        if foreign_key and COLUMN in (definition.get("columns") or []):
            found[name] = foreign_key[0]
    return found


def repoint_provisioning_job(apps, schema_editor):
    EventQnaSession = apps.get_model("events", "EventQnaSession")
    JobIntent = apps.get_model("cb_jobs", "JobIntent")
    connection = schema_editor.connection
    constraints = _field_constraints(connection, TABLE)
    foreign_keys = _foreign_keys(constraints)

    if not foreign_keys:
        # Fresh install: replace the bare uuid column with the full
        # one-to-one column through portable schema-editor operations.
        bare = models.UUIDField(blank=True, null=True, db_column=COLUMN)
        bare.set_attributes_from_name("provisioning_job")
        schema_editor.remove_field(EventQnaSession, bare)
        full = models.OneToOneField(
            blank=True,
            null=True,
            on_delete=models.SET_NULL,
            related_name="qna_provisioning_session",
            to=JobIntent,
        )
        full.set_attributes_from_name("provisioning_job")
        schema_editor.add_field(EventQnaSession, full)
        return

    quote = connection.ops.quote_name
    for name, referenced_table in foreign_keys.items():
        if referenced_table != TARGET_TABLE:
            # A deployed database: the only legacy key on this column
            # references the read-only donor table the retired jobs app
            # left behind.
            schema_editor.execute(f"ALTER TABLE {quote(TABLE)} DROP CONSTRAINT {quote(name)}")
    if TARGET_TABLE not in foreign_keys.values():
        schema_editor.execute(
            f"ALTER TABLE {quote(TABLE)} ADD CONSTRAINT "
            f"{quote('events_eventqna_provisioning_job_intent_fk')} "
            f"FOREIGN KEY ({quote(COLUMN)}) "
            f"REFERENCES {quote(TARGET_TABLE)} ({quote('id')}) "
            f"DEFERRABLE INITIALLY DEFERRED"
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
