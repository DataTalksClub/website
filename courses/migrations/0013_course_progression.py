import courses.models.cohort
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("courses", "0012_course_prerequisites")]

    operations = [
        migrations.AddField(
            model_name="course",
            name="progression",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text=(
                    "Three ordered learner-journey scenes authored by the course repository."
                ),
                validators=[courses.models.cohort.validate_course_progression],
            ),
        ),
    ]
