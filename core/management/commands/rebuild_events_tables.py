"""Drop the legacy site ``events`` tables so the package app can migrate in.

P5 rebuild for the D4.1 cutover: the site's ``events`` app and the package's
``community_base.events`` share the ``events`` label but not a schema, so the
old tables are dropped and rebuilt from the package migrations instead of
altered in place.  Run it once, after the site app is removed from
``INSTALLED_APPS`` and the package app is installed, and before ``migrate``.

Dry-run is the default and only reports what it found.  ``--apply`` does the
work inside one transaction: drop every ``events_`` table, then clear the
migration records, content types, and their permissions for the label so
``migrate`` starts clean.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandParser
from django.db import connection, transaction

_REGISTRY_DELETES = (
    # The site-owned provenance rows describe the legacy table's identity and
    # are re-created by the reviewed import; they go before the tables their
    # foreign keys point into (D4.1).
    ("content_eventsource", "DELETE FROM content_eventsource"),
    # Permissions next: they reference the content types being removed.
    (
        "auth_permission",
        "DELETE FROM auth_permission WHERE content_type_id IN "
        "(SELECT id FROM django_content_type WHERE app_label = 'events')",
    ),
    ("django_content_type", "DELETE FROM django_content_type WHERE app_label = 'events'"),
    ("django_migrations", "DELETE FROM django_migrations WHERE app = 'events'"),
)


class Command(BaseCommand):
    help = (
        "Drop the legacy events_* tables and clear the events app registry "
        "(P5 rebuild, one-off for the D4.1 cutover)"
    )

    requires_system_checks: list[str] = []

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Drop the tables and registry rows; without this the command only reports.",
        )

    def handle(self, *args: object, **options: object) -> None:
        del args
        apply_changes = bool(options["apply"])

        with connection.cursor() as cursor:
            names = connection.introspection.table_names(cursor)
            # The shared app's own tables (events_event, ...) also start with
            # events_; only tables the current migration graph does not own are
            # legacy leftovers, so they -- and only they -- get dropped.
            owned = set(connection.introspection.django_table_names(only_existing=True))
            tables = [name for name in names if name.startswith("events_") and name not in owned]
            registry_counts = {
                table: cursor.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {column} = 'events'"  # noqa: S608 -- table and column are code-owned literals.
                ).fetchone()[0]
                for table, column in (
                    ("django_migrations", "app"),
                    ("django_content_type", "app_label"),
                )
            }

        mode = "applying" if apply_changes else "dry run"
        self.stdout.write(f"{mode}: {len(tables)} events_ tables")
        for name in tables:
            self.stdout.write(f"  table {name}")
        for table, count in registry_counts.items():
            self.stdout.write(f"  {table}: {count} rows for app 'events'")

        if not apply_changes:
            self.stdout.write("Dry run only; pass --apply to drop and clear.")
            return

        with transaction.atomic():
            # The satellite tables keep live foreign keys into events_event until
            # their post-swap migrations repoint them, so constraint enforcement
            # is off for the drops (SQLite pragma; Postgres uses DROP CASCADE).
            constraints_disabled = connection.disable_constraint_checking()
            try:
                with connection.cursor() as cursor:
                    for name in tables:
                        quoted = connection.ops.quote_name(name)
                        cascade = " CASCADE" if connection.vendor == "postgresql" else ""
                        cursor.execute(f"DROP TABLE IF EXISTS {quoted}{cascade}")  # noqa: S608 -- identifier is quoted and introspected.
                # The migration/content-type/permission rows describe the
                # legacy app; they are cleared only when the legacy identity
                # table itself is going.  If the shared app already owns
                # events_event (an already-cut-over database), its registry
                # rows must stay.
                if "events_event" in tables:
                    with connection.cursor() as cursor:
                        for _table, statement in _REGISTRY_DELETES:
                            cursor.execute(statement)
            finally:
                if constraints_disabled:
                    connection.enable_constraint_checking()

        self.stdout.write(
            self.style.SUCCESS(f"Dropped {len(tables)} tables and cleared the events registry.")
        )
