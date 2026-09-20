from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('courses', '0020_project_repo_enrichment'),
    ]

    operations = [
        migrations.AddField(
            model_name='projectrepoenrichment',
            name='structured',
            field=models.JSONField(blank=True, help_text='course-structured-v1 record extracted from the repository README by the ingest run; null when no structured extraction exists.', null=True),
        ),
    ]
