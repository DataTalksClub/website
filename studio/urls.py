from django.urls import path

from . import views

app_name = "studio"

urlpatterns = [
    path("", views.home, name="home"),
    path("settings", views.site_settings, name="settings"),
    path("access/api-credentials/", views.credential_list, name="credential-list"),
    path(
        "access/api-credentials/<uuid:credential_id>/rotate/",
        views.credential_rotate,
        name="credential-rotate",
    ),
    path(
        "access/api-credentials/<uuid:credential_id>/revoke/",
        views.credential_revoke,
        name="credential-revoke",
    ),
    path("audit/", views.audit_list, name="audit-list"),
    path("audit/<uuid:event_id>/", views.audit_detail, name="audit-detail"),
    path(
        "events/historical-registration-totals/",
        views.historical_registration_list,
        name="historical-registration-list",
    ),
    path(
        "events/historical-registration-totals/mappings/",
        views.historical_registration_mappings,
        name="historical-registration-mappings",
    ),
    path(
        "events/historical-registration-totals/<uuid:run_id>/",
        views.historical_registration_detail,
        name="historical-registration-detail",
    ),
    path(
        "events/historical-registration-totals/<uuid:run_id>/<str:action>/",
        views.historical_registration_action,
        name="historical-registration-action",
    ),
    path(
        "events/<slug:canonical_key>/registration-total/",
        views.historical_registration_total,
        name="historical-registration-total",
    ),
]
