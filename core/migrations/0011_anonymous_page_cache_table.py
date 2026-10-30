"""Create the shared table behind CACHES["anonymous_pages"] (core.page_cache).

The anonymous page cache stores its pages in the database so gunicorn worker
recycles and container restarts cannot drop them.  Django's DatabaseCache
expects its table to exist; building it through the same code path
``manage.py createcachetable`` uses keeps the schema exactly on the installed
Django version, and doing it as a migration means the web-migrate entrypoint,
the worker and every test database get the table from ``migrate`` alone.

The table name is a literal because a migration must not import mutable
runtime code (test_support.tests.test_migrations); the name is pinned to
``website.settings.base.CACHES["anonymous_pages"]["LOCATION"]`` and
``core.page_cache.PAGE_CACHE_TABLE`` by a test in
core/tests/test_anonymous_page_cache.py.
"""

from django.db import migrations

TABLE_NAME = "django_anonymous_page_cache"


def create_cache_table(apps, schema_editor) -> None:
    from django.core.management.commands.createcachetable import Command

    command = Command()
    command.verbosity = 0
    command.create_table(schema_editor.connection.alias, TABLE_NAME, dry_run=False)


def drop_cache_table(apps, schema_editor) -> None:
    schema_editor.execute(
        f"DROP TABLE IF EXISTS {schema_editor.connection.ops.quote_name(TABLE_NAME)}"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0010_mail_template_publication"),
    ]

    operations = [
        migrations.RunPython(create_cache_table, reverse_code=drop_cache_table),
    ]
