import django.db.models.deletion
from django.db import migrations, models


def install_management_principal_retention_guard(apps, schema_editor):
    del apps
    if schema_editor.connection.vendor != "postgresql":
        return
    quote = schema_editor.connection.ops.quote_name
    function = "dtc_core_audit_append_only"
    schema_editor.execute(
        f"""
        CREATE OR REPLACE FUNCTION {quote(function)}() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                IF OLD.actor_id IS NOT NULL
                   AND NEW.actor_id IS NULL
                   AND (to_jsonb(NEW) - 'actor_id')
                       IS NOT DISTINCT FROM (to_jsonb(OLD) - 'actor_id')
                THEN
                    RETURN NEW;
                END IF;
                IF OLD.api_principal_id IS NOT NULL
                   AND NEW.api_principal_id IS NULL
                   AND (to_jsonb(NEW) - 'api_principal_id')
                       IS NOT DISTINCT FROM (to_jsonb(OLD) - 'api_principal_id')
                THEN
                    RETURN NEW;
                END IF;
            END IF;
            RAISE EXCEPTION 'append-only evidence cannot be changed'
                USING ERRCODE = '55000';
        END;
        $$
        """
    )


def restore_actor_retention_guard(apps, schema_editor):
    del apps
    if schema_editor.connection.vendor != "postgresql":
        return
    quote = schema_editor.connection.ops.quote_name
    function = "dtc_core_audit_append_only"
    schema_editor.execute(
        f"""
        CREATE OR REPLACE FUNCTION {quote(function)}() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                IF OLD.actor_id IS NOT NULL
                   AND NEW.actor_id IS NULL
                   AND (to_jsonb(NEW) - 'actor_id')
                       IS NOT DISTINCT FROM (to_jsonb(OLD) - 'actor_id')
                THEN
                    RETURN NEW;
                END IF;
            END IF;
            RAISE EXCEPTION 'append-only evidence cannot be changed'
                USING ERRCODE = '55000';
        END;
        $$
        """
    )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_studio_foundation"),
        ("management_auth", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditevent",
            name="api_principal",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="audit_events",
                to="management_auth.apiprincipal",
            ),
        ),
        migrations.AddField(
            model_name="operation",
            name="api_principal",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="operations",
                to="management_auth.apiprincipal",
            ),
        ),
        migrations.RunPython(
            install_management_principal_retention_guard,
            restore_actor_retention_guard,
        ),
    ]
