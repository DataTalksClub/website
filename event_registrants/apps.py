from django.apps import AppConfig


class EventRegistrantsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "event_registrants"
    verbose_name = "Event registrants"

    def ready(self) -> None:
        import event_registrants.checks  # noqa: F401
