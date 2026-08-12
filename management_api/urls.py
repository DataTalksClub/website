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


@csrf_exempt
def historical_import_collection(request, *args, **kwargs):
    if request.method in {"GET", "HEAD"}:
        return views.historical_import_list(request, *args, **kwargs)
    return views.historical_import_create(request, *args, **kwargs)


historical_import_collection.management_capability_keys = (  # type: ignore[attr-defined]
    "events.historical_registration_import.manage",
    "events.historical_registration_import.create",
)
historical_import_collection.management_capability_views = {  # type: ignore[attr-defined]
    "GET": views.historical_import_list,
    "POST": views.historical_import_create,
}


@csrf_exempt
def historical_mapping_collection(request, *args, **kwargs):
    if request.method in {"GET", "HEAD"}:
        return views.historical_mapping_list(request, *args, **kwargs)
    return views.historical_mapping_create(request, *args, **kwargs)


historical_mapping_collection.management_capability_keys = (  # type: ignore[attr-defined]
    "events.historical_registration_mapping.manage",
    "events.historical_registration_mapping.create",
)
historical_mapping_collection.management_capability_views = {  # type: ignore[attr-defined]
    "GET": views.historical_mapping_list,
    "POST": views.historical_mapping_create,
}


event_identity_collection = csrf_exempt(views.event_identity_list)
event_identity_collection.management_capability_keys = (  # type: ignore[attr-defined]
    "events.identity.read",
)
event_identity_collection.management_capability_views = {  # type: ignore[attr-defined]
    "GET": views.event_identity_list,
}


@csrf_exempt
def site_settings_collection(request, *args, **kwargs):
    if request.method in {"GET", "HEAD"}:
        return views.site_settings_read(request, *args, **kwargs)
    return views.site_settings_write(request, *args, **kwargs)


site_settings_collection.management_capability_keys = (  # type: ignore[attr-defined]
    "site.settings.read",
    "site.settings.write",
)
site_settings_collection.management_capability_views = {  # type: ignore[attr-defined]
    "GET": views.site_settings_read,
    "POST": views.site_settings_write,
}

urlpatterns = [
    path("health", views.admin_health, name="admin-health"),
    path("settings", site_settings_collection, name="admin-site-settings"),
    path("credentials", credential_collection, name="admin-credential-list"),
    path(
        "historical-registration-imports",
        historical_import_collection,
        name="historical-registration-import-list",
    ),
    path(
        "historical-registration-imports/<uuid:run_id>",
        views.historical_import_detail,
        name="historical-registration-import-detail",
    ),
    path(
        "historical-registration-imports/<uuid:run_id>/dry-run",
        views.historical_import_dry_run,
        name="historical-registration-import-dry-run",
    ),
    path(
        "historical-registration-imports/<uuid:run_id>/validate",
        views.historical_import_validate,
        name="historical-registration-import-validate",
    ),
    path(
        "historical-registration-imports/<uuid:run_id>/activate",
        views.historical_import_activate,
        name="historical-registration-import-activate",
    ),
    path(
        "historical-registration-imports/<uuid:run_id>/cancel",
        views.historical_import_cancel,
        name="historical-registration-import-cancel",
    ),
    path(
        "historical-registration-imports/<uuid:run_id>/rollback",
        views.historical_import_rollback,
        name="historical-registration-import-rollback",
    ),
    path(
        "historical-event-mappings",
        historical_mapping_collection,
        name="historical-event-mapping-list",
    ),
    path(
        "historical-event-mappings/<uuid:mapping_id>",
        views.historical_mapping_update,
        name="historical-event-mapping-detail",
    ),
    path(
        "events/<uuid:event_id>/registration-total",
        views.historical_registration_total,
        name="historical-registration-total",
    ),
    path(
        "events/identities",
        event_identity_collection,
        name="event-identity-list",
    ),
    path(
        "events/identities/<uuid:event_id>",
        views.event_identity_detail,
        name="event-identity-detail",
    ),
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
