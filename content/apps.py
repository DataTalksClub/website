from django.apps import AppConfig
from django.core.checks import Error, register
from django.core.exceptions import ImproperlyConfigured


@register()
def review_projection_check(app_configs, **kwargs):
    del app_configs, kwargs
    from .review_projection import review_projection

    try:
        review_projection()
    except ImproperlyConfigured as exc:
        return [
            Error(
                str(exc),
                id="content.E001",
                hint="Restore the pinned review projection and its inventory revisions.",
            )
        ]
    return []


class ContentConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "content"
