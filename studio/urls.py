from django.urls import path

from . import views

app_name = "studio"

urlpatterns = [
    path("", views.home, name="home"),
    path("settings", views.site_settings, name="settings"),
    path("navigation", views.site_navigation, name="navigation"),
    path("sponsors/", views.sponsors, name="sponsor-list"),
    path("sponsors/export/", views.sponsor_export, name="sponsor-export"),
    path("sponsors/<uuid:sponsor_id>/", views.sponsor_detail, name="sponsor-detail"),
    path(
        "sponsors/<uuid:sponsor_id>/archive/",
        views.sponsor_archive,
        name="sponsor-archive",
    ),
    path(
        "sponsors/<uuid:sponsor_id>/reactivate/",
        views.sponsor_reactivate,
        name="sponsor-reactivate",
    ),
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
    path("events/identities/", views.event_identity_list, name="event-identity-list"),
    path(
        "events/identities/<uuid:event_id>/",
        views.event_identity_detail,
        name="event-identity-detail",
    ),
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
        "events/<uuid:event_id>/registration-total/",
        views.historical_registration_total,
        name="historical-registration-total",
    ),
    path(
        "courses/registration-count-baselines/",
        views.course_registration_count_list,
        name="course-registration-count-list",
    ),
    path(
        "courses/registration-count-baselines/<uuid:run_id>/",
        views.course_registration_count_detail,
        name="course-registration-count-detail",
    ),
    path(
        "courses/registration-count-baselines/<uuid:run_id>/<str:action>/",
        views.course_registration_count_action,
        name="course-registration-count-action",
    ),
    path(
        "courses/registration-campaigns/<slug:campaign_slug>/public-count/",
        views.course_registration_count_total,
        name="course-registration-count-total",
    ),
]
