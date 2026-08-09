from django.urls import path
from django.views.decorators.csrf import csrf_exempt

from . import views


@csrf_exempt
def credential_collection(request, *args, **kwargs):
    if request.method in {"GET", "HEAD"}:
        return views.credential_list(request, *args, **kwargs)
    return views.credential_create(request, *args, **kwargs)


credential_collection.management_capability_keys = (  # type: ignore[attr-defined]
    "management.credentials.list",
    "management.credentials.create",
)
credential_collection.management_capability_views = {  # type: ignore[attr-defined]
    "GET": views.credential_list,
    "POST": views.credential_create,
}

urlpatterns = [
    path("health", views.admin_health, name="admin-health"),
    path("credentials", credential_collection, name="admin-credential-list"),
    path(
        "credentials/<uuid:credential_id>/rotate",
        views.credential_rotate,
        name="admin-credential-rotate",
    ),
    path(
        "credentials/<uuid:credential_id>/revoke",
        views.credential_revoke,
        name="admin-credential-revoke",
    ),
]
