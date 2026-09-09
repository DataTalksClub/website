# Source content IDs become compound strings (course-slug-namespaced slug
# segments) instead of UUIDs.  Postgres cannot cast uuid columns to varchar
# without an explicit USING clause, and the run-of-the-mill AlterField emits
# none, so the real type change happens in CastSourceContentIdColumns on
# Postgres while SQLite takes the regular table-rebuild path.
import django.core.validators
from django.db import migrations, models

COMPOUND_ID_VALIDATOR = django.core.validators.RegexValidator(
    "^[a-z0-9]+(?:-[a-z0-9]+)*(?:/[a-z0-9]+(?:-[a-z0-9]+)*)*$",
    "Enter a lowercase compound content ID: slug segments separated by slashes, "
    "namespaced by the course slug.",
)


def _source_content_id_field() -> models.CharField:
    return models.CharField(
        blank=True,
        max_length=255,
        null=True,
        validators=[COMPOUND_ID_VALIDATOR],
    )


def _cast_source_content_id(upcast: bool) -> str:
    cast = "::text" if upcast else "::uuid"
    target_type = "varchar(255)" if upcast else "uuid"
    source_type = "uuid" if upcast else "character varying"
    return f"""
DO $$
DECLARE
    column_record record;
BEGIN
    FOR column_record IN
        SELECT c.table_name
        FROM information_schema.columns AS c
        WHERE c.table_schema = current_schema()
          AND c.column_name = 'source_content_id'
          AND c.data_type = '{source_type}'
    LOOP
        EXECUTE format(
            'ALTER TABLE %I ALTER COLUMN source_content_id TYPE {target_type} '
            'USING source_content_id{cast}',
            column_record.table_name
        );
    END LOOP;
END
$$;
"""


class CastSourceContentIdColumns(migrations.RunSQL):
    """Vendor-scoped column cast; SQLite is handled by the AlterFields below."""

    def __init__(self) -> None:
        super().__init__(
            sql=_cast_source_content_id(upcast=True),
            reverse_sql=_cast_source_content_id(upcast=False),
        )

    def _applies(self, schema_editor: object) -> bool:
        connection = schema_editor.connection
        return connection.vendor == "postgresql"

    def database_forwards(self, app_label, schema_editor, from_state, to_state) -> None:
        if self._applies(schema_editor):
            super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state) -> None:
        if self._applies(schema_editor):
            super().database_backwards(app_label, schema_editor, from_state, to_state)


class AlterSourceContentIdField(migrations.AlterField):
    """AlterField whose DDL runs everywhere except Postgres (already cast)."""

    def database_forwards(self, app_label, schema_editor, from_state, to_state) -> None:
        if schema_editor.connection.vendor != "postgresql":
            super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state) -> None:
        if schema_editor.connection.vendor != "postgresql":
            super().database_backwards(app_label, schema_editor, from_state, to_state)


class Migration(migrations.Migration):
    dependencies = [
        ("courses", "0005_shared_current_curriculum"),
    ]

    operations = [
        CastSourceContentIdColumns(),
        # The reverse cast back to uuid fails loudly once compound IDs are
        # stored: they have no UUID representation, so reversing is only
        # possible on data that never left the UUID domain.
        AlterSourceContentIdField(
            model_name="cohort",
            name="source_content_id",
            field=_source_content_id_field(),
        ),
        AlterSourceContentIdField(
            model_name="course",
            name="source_content_id",
            field=_source_content_id_field(),
        ),
        AlterSourceContentIdField(
            model_name="homework",
            name="source_content_id",
            field=_source_content_id_field(),
        ),
        AlterSourceContentIdField(
            model_name="module",
            name="source_content_id",
            field=_source_content_id_field(),
        ),
        AlterSourceContentIdField(
            model_name="question",
            name="source_content_id",
            field=_source_content_id_field(),
        ),
        AlterSourceContentIdField(
            model_name="sharedcurriculum",
            name="source_content_id",
            field=_source_content_id_field(),
        ),
        AlterSourceContentIdField(
            model_name="sharedcurriculumasset",
            name="source_content_id",
            field=_source_content_id_field(),
        ),
        AlterSourceContentIdField(
            model_name="sharedlesson",
            name="source_content_id",
            field=_source_content_id_field(),
        ),
        AlterSourceContentIdField(
            model_name="sharedmodule",
            name="source_content_id",
            field=_source_content_id_field(),
        ),
        AlterSourceContentIdField(
            model_name="unit",
            name="source_content_id",
            field=_source_content_id_field(),
        ),
    ]
