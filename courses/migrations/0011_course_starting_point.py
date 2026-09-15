from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("courses", "0010_restore_ai_dev_tools_zoomcamp_family_slug"),
    ]

    operations = [
        migrations.AddField(
            model_name="course",
            name="starting_point",
            field=models.TextField(
                blank=True,
                default="",
                help_text=(
                    "Optional learner starting point for the course landing page. "
                    "Managed here and preserved by curriculum imports."
                ),
            ),
        ),
    ]
