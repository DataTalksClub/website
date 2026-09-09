"""Deployment checks for the built-in admin and loginas management surface."""

from importlib import import_module

from django.conf import settings
from django.core.checks import Error, Tags, register
from django.urls import URLResolver

from core.bootstrap import RuntimeEnvironment


def admin_route_names(urlpatterns) -> set[str]:
    """Collect the mounted admin/loginas route identities, recursively."""
    names: set[str] = set()
    stack = list(urlpatterns)
    while stack:
        pattern = stack.pop()
        if not isinstance(pattern, URLResolver):
            continue
        urlconf = pattern.urlconf_module
        if isinstance(urlconf, str):
            module_name = urlconf
        else:
            module_name = getattr(urlconf, "__name__", "")
        if module_name == "loginas.urls":
            names.add("loginas")
        if getattr(pattern, "namespace", "") == "admin":
            names.add("admin")
        stack.extend(pattern.url_patterns)
    return names


@register(Tags.security)
def check_production_admin_exposure(app_configs: object, **kwargs: object) -> list[Error]:
    """Reject production deployments that expose or misgate the admin surface."""
    del app_configs, kwargs
    if settings.RUNTIME_ENVIRONMENT is not RuntimeEnvironment.PRODUCTION:
        return []
    mounted = admin_route_names(import_module(settings.ROOT_URLCONF).urlpatterns)
    if "admin" not in mounted and "loginas" not in mounted:
        return []
    if not settings.ADMIN_BREAK_GLASS:
        return [
            Error(
                "The Django admin and loginas routes are mounted while "
                "ADMIN_BREAK_GLASS is disabled.",
                id="website.E001",
                hint=(
                    "Remove the admin mounts or explicitly enable "
                    "DJANGO_ADMIN_BREAK_GLASS under review."
                ),
            )
        ]
    if "website.admin_gate.BreakGlassAdminGateMiddleware" not in settings.MIDDLEWARE:
        return [
            Error(
                "ADMIN_BREAK_GLASS is enabled without the break-glass admin gate middleware.",
                id="website.E002",
                hint=(
                    "Add website.admin_gate.BreakGlassAdminGateMiddleware "
                    "after the authentication middleware."
                ),
            )
        ]
    return []
