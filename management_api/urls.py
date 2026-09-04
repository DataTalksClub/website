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


@csrf_exempt
def operational_settings_collection(request, *args, **kwargs):
    if request.method in {"GET", "HEAD"}:
        return views.operational_settings_read(request, *args, **kwargs)
    return views.operational_settings_write(request, *args, **kwargs)


operational_settings_collection.management_capability_keys = (  # type: ignore[attr-defined]
    "settings.operational.read",
    "settings.operational.write",
)
operational_settings_collection.management_capability_views = {  # type: ignore[attr-defined]
    "GET": views.operational_settings_read,
    "PATCH": views.operational_settings_write,
}


oauth_provider_collection = csrf_exempt(views.oauth_provider_list)
oauth_provider_collection.management_capability_keys = (  # type: ignore[attr-defined]
    "accounts.oauth_providers.read",
)
oauth_provider_collection.management_capability_views = {  # type: ignore[attr-defined]
    "GET": views.oauth_provider_list,
}


oauth_provider_item = csrf_exempt(views.oauth_provider_update)
oauth_provider_item.management_capability_keys = (  # type: ignore[attr-defined]
    "accounts.oauth_providers.write",
)
oauth_provider_item.management_capability_views = {  # type: ignore[attr-defined]
    "PUT": views.oauth_provider_update,
}


@csrf_exempt
def site_navigation_collection(request, *args, **kwargs):
    if request.method in {"GET", "HEAD"}:
        return views.site_navigation_read(request, *args, **kwargs)
    return views.site_navigation_write(request, *args, **kwargs)


site_navigation_collection.management_capability_keys = (  # type: ignore[attr-defined]
    "site.navigation.read",
    "site.navigation.write",
)
site_navigation_collection.management_capability_views = {  # type: ignore[attr-defined]
    "GET": views.site_navigation_read,
    "PUT": views.site_navigation_write,
}


@csrf_exempt
def sponsor_collection(request, *args, **kwargs):
    if request.method in {"GET", "HEAD"}:
        return views.sponsor_list(request, *args, **kwargs)
    return views.sponsor_create(request, *args, **kwargs)


sponsor_collection.management_capability_keys = (  # type: ignore[attr-defined]
    "site.sponsors.read",
    "site.sponsors.write",
)
sponsor_collection.management_capability_views = {  # type: ignore[attr-defined]
    "GET": views.sponsor_list,
    "POST": views.sponsor_create,
}


@csrf_exempt
def sponsor_item(request, *args, **kwargs):
    if request.method in {"GET", "HEAD"}:
        return views.sponsor_detail(request, *args, **kwargs)
    return views.sponsor_update(request, *args, **kwargs)


sponsor_item.management_capability_keys = (  # type: ignore[attr-defined]
    "site.sponsors.detail",
    "site.sponsors.update",
)
sponsor_item.management_capability_views = {  # type: ignore[attr-defined]
    "GET": views.sponsor_detail,
    "PATCH": views.sponsor_update,
}


@csrf_exempt
def event_qna_collection(request, *args, **kwargs):
    if request.method in {"GET", "HEAD"}:
        return views.event_qna_read(request, *args, **kwargs)
    return views.event_qna_manage(request, *args, **kwargs)


event_qna_collection.management_capability_keys = (  # type: ignore[attr-defined]
    "events.qna.read",
    "events.qna.manage",
)
event_qna_collection.management_capability_views = {  # type: ignore[attr-defined]
    "GET": views.event_qna_read,
    "PATCH": views.event_qna_manage,
}


urlpatterns = [
    path("health", views.admin_health, name="admin-health"),
    path("settings", site_settings_collection, name="admin-site-settings"),
    path(
        "settings/operational",
        operational_settings_collection,
        name="admin-operational-settings",
    ),
    path("auth/providers", oauth_provider_collection, name="admin-oauth-provider-list"),
    path(
        "auth/providers/<str:provider>",
        oauth_provider_item,
        name="admin-oauth-provider-detail",
    ),
    path("navigation", site_navigation_collection, name="admin-site-navigation"),
    path("sponsors", sponsor_collection, name="admin-sponsor-list"),
    path("sponsors/<uuid:sponsor_id>", sponsor_item, name="admin-sponsor-detail"),
    path(
        "sponsors/<uuid:sponsor_id>/archive",
        views.sponsor_archive,
        name="admin-sponsor-archive",
    ),
    path(
        "sponsors/<uuid:sponsor_id>/reactivate",
        views.sponsor_reactivate,
        name="admin-sponsor-reactivate",
    ),
    path(
        "sponsor-directory-exports",
        views.sponsor_export,
        name="admin-sponsor-export",
    ),
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
        "events/<uuid:event_id>/qna",
        event_qna_collection,
        name="admin-event-qna-read",
    ),
    path(
        "events/<uuid:event_id>/qna/questions/<str:question_id>",
        views.event_qna_moderate,
        name="admin-event-qna-moderate",
    ),
    path(
        "events/<uuid:event_id>/qna/retry",
        views.event_qna_retry,
        name="admin-event-qna-retry",
    ),
    path(
        "events/<uuid:event_id>/qna/cohosts",
        views.event_qna_cohost_create,
        name="admin-event-qna-cohost-create",
    ),
    path(
        "events/<uuid:event_id>/qna/cohosts/<str:invite_id>",
        views.event_qna_cohost_revoke,
        name="admin-event-qna-cohost-revoke",
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
