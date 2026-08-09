import django.db.models.deletion
from django.db import migrations, models


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

    ]
