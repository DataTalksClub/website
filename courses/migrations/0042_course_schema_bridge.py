import uuid

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


COURSE_FAMILY_NAMESPACE = uuid.UUID("6f6e31c5-221a-4fc7-9d5b-1cf7b0a92201")
COHORT_NAMESPACE = uuid.UUID("f07c0f6c-2cb4-4bb3-a44b-8c8f9d2bf4d2")


def backfill_legacy_course_families(apps, schema_editor):
    """Create one stable family for each deployed legacy course edition.

    The legacy ``Course`` row becomes the current ``Cohort`` row.  There is
    no source-level family mapping at this boundary, so the safe deterministic
    mapping is one family per legacy primary key.  UUIDs, slugs, and updates
    are stable across a failed/retried migration and never recreate a cohort.
    """

    Course = apps.get_model("courses", "Course")
    Cohort = apps.get_model("courses", "Cohort")
    database = schema_editor.connection.alias

    for cohort in Cohort.objects.using(database).order_by("pk").iterator():
        family_id = uuid.uuid5(COURSE_FAMILY_NAMESPACE, f"legacy-course:{cohort.pk}")
        cohort_uuid = uuid.uuid5(COHORT_NAMESPACE, f"legacy-cohort:{cohort.pk}")
        family_defaults = {
            "slug": f"legacy-{cohort.pk}",
            "title": cohort.title,
            "description": cohort.description,
            "outcome": cohort.outcome or "",
            "github_repo_url": cohort.github_repo_url or "",
            "docs_url": "",
            "faq_document_url": cohort.faq_document_url or "",
            "social_media_hashtag": cohort.social_media_hashtag or "",
            "visible": cohort.visible,
        }
        Course.objects.using(database).update_or_create(
            pk=family_id,
            defaults=family_defaults,
        )

        updates = {
            "uuid": cohort_uuid,
            "year": 2026,
            "outcome": cohort.outcome or "",
            "course_id": family_id,
        }
        Cohort.objects.using(database).filter(pk=cohort.pk).update(**updates)


class Migration(migrations.Migration):
    dependencies = [
        ("courses", "0041_courseregistrationcountsourcerun_and_more"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RenameModel(
                    old_name="Course",
                    new_name="Cohort",
                ),
                migrations.AlterModelTable(
                    name="cohort",
                    table="courses_course",
                ),
            ],
        ),
        migrations.CreateModel(
            name="Course",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("slug", models.SlugField(unique=True)),
                ("title", models.CharField(max_length=200)),
                ("description", models.TextField(blank=True)),
                ("outcome", models.TextField(blank=True, default="")),
                (
                    "github_repo_url",
                    models.URLField(
                        blank=True,
                        help_text="Optional repository shared by the course family.",
                        validators=[django.core.validators.URLValidator()],
                    ),
                ),
                (
                    "docs_url",
                    models.URLField(
                        blank=True,
                        help_text="Optional documentation shared by the course family.",
                        validators=[django.core.validators.URLValidator()],
                    ),
                ),
                (
                    "faq_document_url",
                    models.URLField(
                        blank=True,
                        help_text="Optional FAQ shared by the course family.",
                        validators=[django.core.validators.URLValidator()],
                    ),
                ),
                (
                    "social_media_hashtag",
                    models.CharField(
                        blank=True,
                        help_text="The hashtag associated with the course family.",
                        max_length=100,
                    ),
                ),
                ("visible", models.BooleanField(default=True)),
            ],
            options={"db_table": "courses_course_family"},
        ),
        migrations.AddField(
            model_name="cohort",
            name="uuid",
            field=models.UUIDField(editable=False, null=True),
        ),
        migrations.AddField(
            model_name="cohort",
            name="year",
            field=models.PositiveIntegerField(default=2026, null=True),
        ),
        migrations.AddField(
            model_name="cohort",
            name="outcome",
            field=models.TextField(blank=True, default="", null=True),
        ),
        migrations.AddField(
            model_name="cohort",
            name="course",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="cohorts",
                to="courses.course",
            ),
        ),
        migrations.RunPython(
            backfill_legacy_course_families,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="cohort",
            name="uuid",
            field=models.UUIDField(
                default=uuid.uuid4,
                editable=False,
                unique=True,
            ),
        ),
        migrations.AlterField(
            model_name="cohort",
            name="year",
            field=models.PositiveIntegerField(default=2026),
        ),
        migrations.AlterField(
            model_name="cohort",
            name="outcome",
            field=models.TextField(blank=True, db_default="", default=""),
        ),
        migrations.AlterField(
            model_name="cohort",
            name="course",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="cohorts",
                to="courses.course",
            ),
        ),
        migrations.AddConstraint(
            model_name="cohort",
            constraint=models.UniqueConstraint(
                fields=("course", "year"),
                name="courses_cohort_course_year_unique",
            ),
        ),
    ]
