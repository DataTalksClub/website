"""Move the user table to the shared physical name (plan issue D3.1e, P7 step 2).

The state has carried the model name ``User`` since the accounts initial
migration, rewritten in place as part of this change. That rewrite is what
makes the rename possible at all: ``accounts.0001`` itself, and every
third-party migration that depends on ``swappable_dependency(AUTH_USER_MODEL)``
(django.contrib.admin, allauth's account and socialaccount apps), resolves
``settings.AUTH_USER_MODEL`` against historical state at the moment it is
applied. With the setting now reading ``accounts.User`` and a ``RenameModel``
sitting here instead, ``accounts.0001`` fails on a fresh database with
"Related model 'accounts.user' cannot be resolved" before any third-party
migration is even reached. Rewriting an applied migration changes nothing for
an existing database -- Django records only (app, name) in django_migrations,
and the rewritten state matches the schema that database already has.

Until here the table kept its pre-rename physical name
``accounts_customuser``. This migration performs the rename in
one step: ``AlterModelTable`` moves the model table and the two auto-created
M2M through tables to the shared names in both state and database, and the
guarded data operation below fixes the pieces a table rename cannot carry --
the ``customuser_id`` through columns on databases that predate this change,
and the content type row (with its permission codenames) so existing
permission grants keep pointing at the same model.

On databases migrated from scratch every guarded step below is a no-op: the
tables were created under the pre-rename name by ``0001_initial`` and this is
their first move.

Reverses as an operation (``AlterModelTable`` renames the tables back); the
data operation's reverse is deliberately a no-op -- content type rows and
column names are forward-compatibility bookkeeping, and a physical rollback
is a database restore, not a migration reverse.
"""

from django.db import migrations
from django.db.models import Value
from django.db.models.functions import Replace

COLUMN_RENAMES = (
    ("accounts_user_groups", "customuser_id", "user_id"),
    ("accounts_user_user_permissions", "customuser_id", "user_id"),
)


def _column_names(connection, table_name):
    with connection.cursor() as cursor:
        description = connection.introspection.get_table_description(cursor, table_name)
    return {column.name for column in description}


def fix_rename_fallout(apps, schema_editor):
    connection = schema_editor.connection
    quote = connection.ops.quote_name
    for table_name, old_column, new_column in COLUMN_RENAMES:
        columns = _column_names(connection, table_name)
        if old_column not in columns or new_column in columns:
            # Created under the post-rename name already (a database migrated
            # from scratch): nothing to move.
            continue
        schema_editor.execute(
            f"ALTER TABLE {quote(table_name)} "
            f"RENAME COLUMN {quote(old_column)} TO {quote(new_column)}"
        )

    ContentType = apps.get_model("contenttypes", "ContentType")
    ContentType.objects.filter(app_label="accounts", model="customuser").update(model="user")
    user_type_ids = list(
        ContentType.objects.filter(app_label="accounts", model="user").values_list("pk", flat=True)
    )
    if user_type_ids:
        Permission = apps.get_model("auth", "Permission")
        Permission.objects.filter(
            content_type_id__in=user_type_ids,
            codename__endswith="_customuser",
        ).update(codename=Replace("codename", Value("customuser"), Value("user")))


def restore_rename_fallout(apps, schema_editor):
    # Deliberately a no-op: see the docstring.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0008_contract_moved_fields"),
    ]

    operations = [
        migrations.AlterModelTable(name="user", table="accounts_user"),
        migrations.RunPython(fix_rename_fallout, restore_rename_fallout),
    ]
