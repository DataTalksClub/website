"""Create the shared table behind CACHES["anonymous_pages"] (core.page_cache).

The anonymous page cache stores its pages in the database so gunicorn worker
recycles and container restarts cannot drop them.  Django's DatabaseCache
expects its table to exist; building it through the same code path
``manage.py createcachetable`` uses keeps the schema exactly on the installed
Django version, and doing it as a migration means the web-migrate entrypoint,
the worker and every test database get the table from ``migrate`` alone.
"""

from django.db import migrations


def create_cache_table(apps, schema_editor) -> None:
    from django.core.management.commands.createcachetable import Command

    from core.page_cache import PAGE_CACHE_TABLE

    command = Command()
    command.verbosity = 0
    command.create_table(schema_editor.connection.alias, PAGE_CACHE_TABLE, dry_run=False)


def drop_cache_table(apps, schema_editor) -> None:
    from core.page_cache import PAGE_CACHE_TABLE

    schema_editor.execute(
        "DROP TABLE IF EXISTS {}".format(schema_editor.connection.ops.quote_name(PAGE_CACHE_TABLE))
    )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0010_mail_template_publication"),
    ]

    operations = [
        migrations.RunPython(create_cache_table, reverse_code=drop_cache_table),
    ]
