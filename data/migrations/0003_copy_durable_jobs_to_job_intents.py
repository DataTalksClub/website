"""Copy the retired jobs app's durable rows into cb_jobs.JobIntent (D1.1).

The site jobs app is removed in the same change, so this migration speaks
raw SQL against the tables the old app left behind. On a database that
never ran the old app (fresh installs, test databases) the table does not
exist and the copy is skipped. Rows are copied with their ids preserved,
which keeps ``events.EventQnaSession.provisioning_job`` references valid
across the swap. Statuses map onto the package's set:

- ``pending`` -> ``pending``; ``running`` -> ``running``
- ``retry_wait`` -> ``failed`` (claimable again while attempts remain)
- ``succeeded`` -> ``succeeded``
- ``failed`` / ``cancelled`` -> ``dead`` (terminal)

The lease-state check constraint on the target only allows a lease on a
running row, so non-running rows lose any stale lease values. Re-running
is guarded on the target being empty.
"""

from django.db import migrations

INSERT = """
INSERT INTO cb_jobs_jobintent (
    id, handler, key_hash, payload, payload_hash,
    status, attempts, max_attempts, available_at,
    lease_token, lease_expires_at, correlation_id, external_id,
    last_error, created_at, updated_at
)
SELECT
    id, handler, deduplication_key_hash, payload, payload_hash,
    CASE status
        WHEN 'retry_wait' THEN 'failed'
        WHEN 'failed' THEN 'dead'
        WHEN 'cancelled' THEN 'dead'
        ELSE status
    END,
    attempt_count, max_attempts, available_at,
    CASE WHEN status = 'running' THEN lease_token END,
    CASE WHEN status = 'running' THEN lease_expires_at ELSE NULL END,
    correlation_id, '',
    last_error_code, created_at, updated_at
FROM jobs_durablejob
WHERE NOT EXISTS (SELECT 1 FROM cb_jobs_jobintent)
"""


def copy_legacy_rows(apps, schema_editor):
    table_names = set(
        schema_editor.connection.introspection.table_names(schema_editor.connection.cursor())
    )
    if "jobs_durablejob" not in table_names:
        return
    schema_editor.execute(INSERT)


def noop(apps, schema_editor):
    return None


class Migration(migrations.Migration):
    dependencies = [
        ("data", "0002_redact_datamailer_audit_pii"),
        ("cb_jobs", "0002_jobintent_cb_jobs_external_id_unique"),
    ]

    operations = [
        migrations.RunPython(copy_legacy_rows, noop),
    ]
