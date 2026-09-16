from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("courses", "0013_course_progression")]

    operations = [
        migrations.AddField(
            model_name="course",
            name="weekly_commitment",
            field=models.TextField(
                blank=True,
                default="",
                help_text=(
                    "Short factual note on cost and weekly time commitment, shown "
                    "near the family hero. Sourced from the course's own FAQ "
                    "answers and edited directly -- not synced from the course "
                    "repository."
                ),
            ),
        ),
    ]
