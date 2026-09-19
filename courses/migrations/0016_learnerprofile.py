"""The course-platform person fields move off the user model (plan D3.1).

``courses.LearnerProfile`` mirrors the user model definitions verbatim
(same max lengths, null/blank, choices, help texts): this is a data-preserving
expand, with no field-contract change. The user-model columns are removed by
a later ``accounts`` contract migration, after this app's data-copy migration
has run.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("courses", "0015_alter_courseregistration_user_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="LearnerProfile",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "role",
                    models.CharField(
                        choices=[("student", "Student"), ("instructor", "Instructor")],
                        default="student",
                        max_length=10,
                    ),
                ),
                (
                    "certificate_name",
                    models.CharField(
                        blank=True,
                        help_text="Your actual name that will appear on your certificates",
                        max_length=255,
                        null=True,
                        verbose_name="Certificate name",
                    ),
                ),
                (
                    "country",
                    models.CharField(blank=True, max_length=100, verbose_name="Country"),
                ),
                (
                    "region",
                    models.CharField(blank=True, max_length=100, verbose_name="Region"),
                ),
                (
                    "registration_role",
                    models.CharField(
                        blank=True,
                        help_text="Role last used on a course registration form",
                        max_length=40,
                        verbose_name="Registration role",
                    ),
                ),
                (
                    "github_url",
                    models.URLField(blank=True, null=True, verbose_name="GitHub URL"),
                ),
                (
                    "linkedin_url",
                    models.URLField(blank=True, null=True, verbose_name="LinkedIn URL"),
                ),
                (
                    "personal_website_url",
                    models.URLField(
                        blank=True, null=True, verbose_name="Personal website URL"
                    ),
                ),
                (
                    "about_me",
                    models.TextField(blank=True, null=True, verbose_name="About me"),
                ),
                (
                    "dark_mode",
                    models.BooleanField(
                        default=False, help_text="Enable dark mode theme", verbose_name="Dark mode"
                    ),
                ),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="learner_profile",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ("user_id",),
            },
        ),
    ]
