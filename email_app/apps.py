from django.apps import AppConfig


class EmailAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "email_app"

    def ready(self) -> None:
        # Register the durable unsubscribe-replay handler.
        from . import jobs  # noqa: F401
