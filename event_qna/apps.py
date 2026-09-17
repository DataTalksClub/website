from django.apps import AppConfig


class EventQnaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "event_qna"
    verbose_name = "Event Q&A"

    def ready(self) -> None:
        # Register the durable Q&A provisioning intent handler.  The handler
        # name keeps its historical "events.qna.provision" form: stored durable
        # intents reference it, and renaming it would orphan queued work.
        from . import jobs  # noqa: F401
