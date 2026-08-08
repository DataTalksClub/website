from django.apps import AppConfig


class ManagementAPIConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "management_api"

    def ready(self) -> None:
        import management_api.checks  # noqa: F401
