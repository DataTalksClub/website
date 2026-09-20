from django.apps import AppConfig


class StudioConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "studio"

    def ready(self) -> None:
        import accounts.studio_sessions  # noqa: F401
        import studio.checks  # noqa: F401
        from studio.registration import register_dtc_studio_destinations

        register_dtc_studio_destinations()
