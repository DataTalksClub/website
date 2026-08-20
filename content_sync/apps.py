from django.apps import AppConfig


class ContentSyncConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "content_sync"

    def ready(self):
        from . import course_repository_sync  # noqa: F401
