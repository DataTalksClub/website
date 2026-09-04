from django.apps import AppConfig
from django.core.signals import setting_changed
from django.dispatch import receiver


@receiver(setting_changed)
def _drop_runtime_settings_cache(sender: object, **kwargs: object) -> None:
    """Forget resolved values whenever ``django.conf.settings`` is rewritten.

    ``django.conf.settings`` is the third resolution layer, so a value cached
    from it would survive an ``override_settings`` block and answer with the
    setting the test just replaced.  The signal fires only when a setting is
    overridden or restored, which in practice means tests.
    """

    del sender, kwargs
    from core.runtime_config import reset_runtime_settings_cache

    reset_runtime_settings_cache()


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self) -> None:
        """Register the code-owned settings before anything can resolve one.

        Both registries are import side effects, and a setting that is not
        registered cannot be read, written through the admin API, or listed --
        ``core.runtime_config`` raises instead of guessing.  Importing them here
        is what makes the registration unconditional rather than a consequence
        of whichever adapter happened to be imported first.
        """

        from core import operational_settings, site_settings  # noqa: F401
