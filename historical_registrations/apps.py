from django.apps import AppConfig


class HistoricalRegistrationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "historical_registrations"
    verbose_name = "Historical registrations"

    def ready(self) -> None:
        # Register the durable event-total cache-invalidation intent handler,
        # moved here from the former site events app (#412).
        from . import jobs  # noqa: F401
