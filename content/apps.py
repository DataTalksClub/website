from django.apps import AppConfig
from django.core.checks import Error, Warning, register
from django.core.exceptions import ImproperlyConfigured


@register()
def public_media_store_check(app_configs, **kwargs):
    """Fail closed on a deployable media-store configuration that cannot serve.

    A *deployed* environment must read the published objects from the object store: the
    container image no longer carries them, so a deployed workload left on the filesystem
    backend would answer every recorded image with a fail-closed 502.  Both deployed
    environments are covered, not only production, because ``web.dtcdev.click`` runs the
    same image from the same build context.  A local checkout keeps the credential-free
    filesystem backend and is only *warned* when the root has not been hydrated, so a
    fresh clone is told which command to run instead of silently rendering broken images.
    """

    del app_configs, kwargs
    from .media_store import (
        DEPLOYED_ENVIRONMENTS,
        HYDRATE_COMMAND,
        SUPPORTED_BACKENDS,
        media_store_config,
    )

    try:
        config = media_store_config()
    except ImproperlyConfigured as exc:
        return [
            Error(
                str(exc),
                id="content.E003",
                hint="Set PUBLIC_MEDIA_STORE_BACKEND to one of: " + ", ".join(SUPPORTED_BACKENDS),
            )
        ]
    if config.environment in DEPLOYED_ENVIRONMENTS:
        if config.backend != "s3":
            return [
                Error(
                    "Public media must be served from the object store in a deployed environment.",
                    id="content.E004",
                    hint="Set PUBLIC_MEDIA_STORE_BACKEND=s3 for a deployed environment.",
                )
            ]
        if not config.s3_bucket:
            return [
                Error(
                    "The public media object store bucket is not configured.",
                    id="content.E005",
                    hint="Set PUBLIC_MEDIA_S3_BUCKET for the deployed environment.",
                )
            ]
        return []
    if config.backend == "local" and not any(config.local_root.glob("*")):
        return [
            Warning(
                "The local public media root holds no projection objects.",
                id="content.W001",
                hint=f"Hydrate the checkout with: {HYDRATE_COMMAND}",
            )
        ]
    return []


class ContentConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "content"
