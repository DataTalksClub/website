from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('courses', '0007_coursecurriculumimportrun_cohort_source_checksum_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='question',
            name='source_option_ids',
            field=models.JSONField(blank=True, null=True),
        ),
    ]
