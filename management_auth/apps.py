from django.apps import AppConfig


class ManagementAuthConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "management_auth"

    def ready(self) -> None:
        import management_auth.checks  # noqa: F401
        import management_auth.signals  # noqa: F401
