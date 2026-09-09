"""The PendingUnsubscribe copy between the site and package mail app (D1.2a).

The package ``cb_mail.PendingUnsubscribe`` carries the identical columns as
the site model, so the copy maps one to one. The site model stays: D1.2b
still records opt-outs through ``email_app`` and D1.2c retires it only
after the send paths have switched, so forward is a copy, not a move.

Batches of 1000 keep one statement from holding every pending row at
once, and each batch inserts only rows whose id the target is missing, so
re-running after a partial application continues where it stopped instead
of colliding on the token's unique constraint. The copy is skipped on
databases that never ran the donor. ``copy_backward`` mirrors the same
batching in reverse so a rollback restores what the forward pass copied.
"""

BATCH_SIZE = 1000

COLUMNS = (
    "id, unsubscribe_token, token_fingerprint, scope, status,"
    " attempt_count, last_outcome, accepted_at, updated_at"
)

SITE_TABLE = "email_app_pendingunsubscribe"
PACKAGE_TABLE = "cb_mail_pendingunsubscribe"


def copy_forward(apps, schema_editor):
    _copy_guarded(schema_editor, source=SITE_TABLE, target=PACKAGE_TABLE)


def copy_backward(apps, schema_editor):
    _copy_guarded(schema_editor, source=PACKAGE_TABLE, target=SITE_TABLE)


def _copy_guarded(schema_editor, source: str, target: str) -> None:
    for table in (source, target):
        if not _table_exists(schema_editor, table):
            return
    _copy_batches(schema_editor, source=source, target=target)


def _table_exists(schema_editor, table: str) -> bool:
    return table in schema_editor.connection.introspection.table_names(
        schema_editor.connection.cursor()
    )


def _copy_batches(schema_editor, source: str, target: str) -> int:
    insert = (
        f"INSERT INTO {target} ({COLUMNS}) "
        f"SELECT {COLUMNS} FROM {source} "
        f"WHERE id NOT IN (SELECT id FROM {target}) LIMIT {BATCH_SIZE}"
    )
    copied = 0
    while True:
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(insert)
            batch = cursor.rowcount
        if batch is None or batch <= 0:
            return copied
        copied += batch
        if batch < BATCH_SIZE:
            return copied
