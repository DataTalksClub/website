"""PostgreSQL constraint triggers: an event must have a type, a speaker, a
description and a picture.

Schema only -- DDL, no data.  The rule spans three tables (the type rides in
``events_event.tags``, the description is ``events_event.description``, the
speakers are ``events_eventhost`` rows and the picture is
``event_registrants_eventoperatorconfig.banner_image``), so no column constraint
can express it.  Constraint triggers declared ``DEFERRABLE INITIALLY DEFERRED``
check it when the transaction commits, after ``create_event_identity`` has
written the row, its speakers and its picture.  A commit that would leave an
event incomplete -- a new event, an edit, the removal of its last speaker or
of its picture -- fails with SQLSTATE 23514 (check_violation).

The triggers are installed DISABLED.  Production still holds events without a
description or a picture (to be backfilled from Luma), and an enabled trigger
would refuse every later write to them.  Once ``events.publication.
incomplete_events()`` is empty on production, a follow-up migration enables
them (``ALTER TABLE ... ENABLE TRIGGER``; see ``ENABLE_SQL`` below).

SQLite (local development and the test suite) has no constraint triggers; the
operations are no-ops there.  ``events/tests/test_event_completeness.py`` runs
the trigger behaviour against PostgreSQL.
"""

from django.db import migrations

CHECK_FUNCTION = r"""
CREATE OR REPLACE FUNCTION dtc_event_completeness_gaps(target_event_id bigint)
RETURNS text[] LANGUAGE plpgsql STABLE AS $fn$
DECLARE
    row_tags jsonb;
    row_description text;
    gaps text[] := ARRAY[]::text[];
BEGIN
    SELECT tags, description INTO row_tags, row_description
    FROM events_event WHERE id = target_event_id;
    IF NOT FOUND THEN
        RETURN NULL;  -- the event was deleted in this transaction
    END IF;
    IF row_tags IS NULL OR jsonb_typeof(row_tags) <> 'array' OR NOT EXISTS (
        SELECT 1 FROM jsonb_array_elements(row_tags) AS tag
        WHERE jsonb_typeof(tag) = 'string' AND (tag #>> '{}') LIKE 'dtc-type:_%'
    ) THEN
        gaps := gaps || 'type'::text;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM events_eventhost WHERE event_id = target_event_id) THEN
        gaps := gaps || 'speakers'::text;
    END IF;
    IF btrim(coalesce(row_description, '')) = '' THEN
        gaps := gaps || 'description'::text;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM event_registrants_eventoperatorconfig
        WHERE event_id = target_event_id
          AND banner_image IS NOT NULL AND octet_length(banner_image) > 0
    ) THEN
        gaps := gaps || 'picture'::text;
    END IF;
    RETURN gaps;
END;
$fn$;

CREATE OR REPLACE FUNCTION dtc_event_assert_complete(target_event_id bigint)
RETURNS void LANGUAGE plpgsql AS $fn$
DECLARE
    gaps text[];
BEGIN
    gaps := dtc_event_completeness_gaps(target_event_id);
    IF gaps IS NOT NULL AND cardinality(gaps) > 0 THEN
        RAISE EXCEPTION 'event % must have a type, a speaker, a description and a picture; missing: %',
            target_event_id, array_to_string(gaps, ', ')
            USING ERRCODE = 'check_violation',
                  CONSTRAINT = 'dtc_event_complete';
    END IF;
END;
$fn$;

CREATE OR REPLACE FUNCTION dtc_event_complete_on_event() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    PERFORM dtc_event_assert_complete(NEW.id);
    RETURN NULL;
END;
$fn$;

CREATE OR REPLACE FUNCTION dtc_event_complete_on_child() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        PERFORM dtc_event_assert_complete(OLD.event_id);
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        PERFORM dtc_event_assert_complete(NEW.event_id);
    END IF;
    RETURN NULL;
END;
$fn$;
"""

CREATE_TRIGGERS = r"""
CREATE CONSTRAINT TRIGGER dtc_event_complete
    AFTER INSERT OR UPDATE ON events_event
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION dtc_event_complete_on_event();
CREATE CONSTRAINT TRIGGER dtc_event_complete_speakers
    AFTER INSERT OR UPDATE OR DELETE ON events_eventhost
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION dtc_event_complete_on_child();
CREATE CONSTRAINT TRIGGER dtc_event_complete_picture
    AFTER INSERT OR UPDATE OR DELETE ON event_registrants_eventoperatorconfig
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION dtc_event_complete_on_child();
"""

TRIGGERS = (
    ("events_event", "dtc_event_complete"),
    ("events_eventhost", "dtc_event_complete_speakers"),
    ("event_registrants_eventoperatorconfig", "dtc_event_complete_picture"),
)

#: What the follow-up migration runs once production holds no incomplete event.
ENABLE_SQL = "\n".join(f"ALTER TABLE {table} ENABLE TRIGGER {name};" for table, name in TRIGGERS)
DISABLE_SQL = "\n".join(f"ALTER TABLE {table} DISABLE TRIGGER {name};" for table, name in TRIGGERS)

DROP_ALL = (
    "\n".join(f"DROP TRIGGER IF EXISTS {name} ON {table};" for table, name in TRIGGERS)
    + """
DROP FUNCTION IF EXISTS dtc_event_complete_on_child();
DROP FUNCTION IF EXISTS dtc_event_complete_on_event();
DROP FUNCTION IF EXISTS dtc_event_assert_complete(bigint);
DROP FUNCTION IF EXISTS dtc_event_completeness_gaps(bigint);
"""
)


class PostgreSQLOnly(migrations.RunSQL):
    """``RunSQL`` that runs only on PostgreSQL; SQLite has no constraint triggers."""

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor == "postgresql":
            super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor == "postgresql":
            super().database_backwards(app_label, schema_editor, from_state, to_state)


class Migration(migrations.Migration):
    dependencies = [
        ("event_registrants", "0004_eventbanneruploadchunk"),
        ("events", "0004_provisional_eventseries_visibility"),
    ]

    operations = [
        PostgreSQLOnly(
            sql=CHECK_FUNCTION + CREATE_TRIGGERS + DISABLE_SQL,
            reverse_sql=DROP_ALL,
        ),
    ]
