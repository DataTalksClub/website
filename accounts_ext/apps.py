from django.apps import AppConfig


class AccountsExtConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts_ext"

    def ready(self):
        import accounts_ext.signals  # noqa: F401
