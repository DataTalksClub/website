from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("courses", "0011_course_starting_point")]

    operations = [
        migrations.AlterField(
            model_name="course",
            name="starting_point",
            field=models.TextField(
                blank=True,
                default="",
                help_text=(
                    "Optional learner starting point for the course landing page. "
                    "Owned by the course repository when published there; preserved "
                    "when an import omits it."
                ),
            ),
        ),
        migrations.AddField(
            model_name="course",
            name="prerequisites",
            field=models.TextField(
                blank=True,
                default="",
                help_text=(
                    "Prerequisite knowledge authored by the course repository."
                ),
            ),
        ),
    ]
