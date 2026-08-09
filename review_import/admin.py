"""Fail-closed synthetic administrator bootstrap for local content review."""

from django.conf import settings
from django.contrib.auth import get_user_model

from review_import.workflow import SYNTHETIC_ADMIN_EMAIL


def _assert_side_effects_disabled() -> None:
    string_settings = (
        "DATAMAILER_URL",
        "DATAMAILER_API_KEY",
        "DATAMAILER_CLIENT",
        "DATAMAILER_AUDIENCE",
        "DATAMAILER_FROM_EMAIL",
        "DATAMAILER_WEBHOOK_TOKEN",
        "DATAMAILER_IMPORT_S3_BUCKET",
        "DATAMAILER_IMPORT_S3_PREFIX",
        "DATAMAILER_IMPORT_S3_REGION",
    )
    if any(getattr(settings, name, "") for name in string_settings):
        raise RuntimeError("local review provider settings are not disabled")
    if getattr(settings, "DATAMAILER_SYNC_ON_USER_CREATE", True):
        raise RuntimeError("local review contact synchronization is not disabled")
    if getattr(settings, "DATAMAILER_OUTBOX_DISPATCH_IMMEDIATELY", True):
        raise RuntimeError("local review immediate dispatch is not disabled")
    if settings.EMAIL_BACKEND not in {
        "django.core.mail.backends.dummy.EmailBackend",
        "django.core.mail.backends.locmem.EmailBackend",
    }:
        raise RuntimeError("local review email backend is not inert")
    if not settings.Q_CLUSTER.get("sync") or settings.Q_CLUSTER.get("scheduler"):
        raise RuntimeError("local review background jobs are not disabled")
    if not getattr(settings, "LOCAL_REVIEW_OUTBOUND_NETWORK_DISABLED", False):
        raise RuntimeError("local review outbound network is not disabled")
    if any(
        getattr(settings, name, "")
        for name in (
            "CLOUDWATCH_APP_METRIC_REGION",
            "AWS_REGION",
            "AWS_DEFAULT_REGION",
        )
    ):
        raise RuntimeError("local review AWS settings are not disabled")


def create_synthetic_admin(password: str) -> None:
    """Create the one synthetic review administrator without outbound effects."""

    if not password:
        raise ValueError("synthetic administrator password is empty")
    _assert_side_effects_disabled()
    user_model = get_user_model()
    user, _created = user_model.objects.get_or_create(
        email=SYNTHETIC_ADMIN_EMAIL,
        defaults={"is_staff": True, "is_superuser": True, "is_active": True},
    )
    user.is_staff = True
    user.is_superuser = True
    user.is_active = True
    user.set_password(password)
    user.save()
