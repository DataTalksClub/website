"""Copy operational settings into the package config table (D0.1c).

Plan issue D0.1c, step 1. Each ``core.OperationalSetting`` row is copied into
``cb_config.Setting`` keyed by setting key: the value moves unchanged, the
site's ``value_type`` vocabulary maps onto the package's (string→str,
integer→int, boolean→bool, string_list→list, json_object→json), and the
source badge is preserved. The site table stays in place read-only for rollback; the reverse
migration is a no-op that leaves the copy in place, matching the retention
rule (the drop is D0.1d's, after the rollback window).

Idempotence: a key already present in the target is skipped, so re-running
the copy adds zero rows. The whole copy is one transaction, so malformed
input causes zero partial writes. Fresh installs without the legacy table are
skipped, because the guard checks the source table's existence first.
"""

from django.db import migrations

#: The site value-type vocabulary onto the package's, mirroring
#: ``core.configuration.PACKAGE_VALUE_TYPES`` (migrations stay self-contained).
TYPE_MAP = {
    "string": "str",
    "integer": "int",
    "boolean": "bool",
    "string_list": "list",
    "json_object": "json",
}


def copy_to_package_config(apps, schema_editor):
    cursor = schema_editor.connection.cursor()
    names = schema_editor.connection.introspection.table_names(cursor)
    if "core_operationalsetting" not in names or "cb_config_setting" not in names:
        return
    OperationalSetting = apps.get_model("core", "OperationalSetting")
    Setting = apps.get_model("cb_config", "Setting")
    existing = set(Setting.objects.values_list("key", flat=True))
    for row in OperationalSetting.objects.all().order_by("key"):
        if row.key in existing:
            continue
        Setting.objects.create(
            key=row.key,
            value=row.value,
            value_type=TYPE_MAP.get(row.value_type, row.value_type),
            source=row.source,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0007_alter_staffsession_options_staffsession_last_seen_at"),
        ("cb_config", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(copy_to_package_config, migrations.RunPython.noop),
    ]
