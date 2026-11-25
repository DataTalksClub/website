"""Enable the event completeness triggers installed (disabled) by 0005.

Schema only -- DDL, no data.  From here on, PostgreSQL refuses to commit an
event without a type, a speaker, a description and a picture: a new event, an
edit to an existing one, the removal of its last speaker, or the removal of its
picture fails with SQLSTATE 23514 (``dtc_event_complete``).

Enabling a trigger does not re-check existing rows.  An event that is still
incomplete (``events.publication.incomplete_events()``) keeps rendering, but
every later write to it is refused unless that write completes it (for an
event missing only its picture: ``dtc events upload-banner``).

SQLite has no constraint triggers; the operation is a no-op there.  The
model-level rule (``event_registrants.signals`` and
``EventOperatorConfig.save()``/``delete()``) refuses an event without a
picture on every database.
"""

from importlib import import_module

from django.db import migrations

_installed = import_module("event_registrants.migrations.0005_event_completeness_triggers")


class Migration(migrations.Migration):
    dependencies = [
        ("event_registrants", "0006_eventbanneruploadchunk_staged"),
    ]

    operations = [
        _installed.PostgreSQLOnly(sql=_installed.ENABLE_SQL, reverse_sql=_installed.DISABLE_SQL),
    ]
