from django.apps import AppConfig


class SlackAccessConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "slack_access"

    def ready(self):
        from . import signals  # noqa: F401
