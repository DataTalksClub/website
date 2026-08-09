from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("management_auth", "0002_api_rate_subject"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="apiprincipal",
            options={
                "ordering": ("name", "id"),
                "permissions": (
                    ("read_admin_health", "Can read management API health"),
                    ("manage_api_credentials", "Can manage API credentials"),
                ),
            },
        ),
    ]
