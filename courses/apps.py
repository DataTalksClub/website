from django.apps import AppConfig


class CoursesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "courses"

    def ready(self):
        from courses import (  # noqa: F401
            announcement_jobs,
            genmedia_approval,
            registration_jobs,
            scoring_jobs,
        )
