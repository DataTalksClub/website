from django.apps import AppConfig


class EventsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "events"

    def ready(self) -> None:
        # Register the durable cache-invalidation intent handler.
        from . import jobs  # noqa: F401
