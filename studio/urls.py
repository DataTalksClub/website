from django.urls import path

from event_qna import studio_views as qna_views

from . import event_views, launch_views, views

app_name = "studio"

urlpatterns = [
    path("course-launch/", launch_views.launch_read, name="course-launch-read"),
    path("course-launch/commands/", launch_views.launch_write, name="course-launch-write"),
    path("course-launch/dispatch/", launch_views.launch_send, name="course-launch-send"),
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
    path("audit/export/", views.audit_export, name="audit-export"),
    path("audit/<uuid:event_id>/", views.audit_detail, name="audit-detail"),
    path("events/", event_views.event_list, name="event-list"),
    path("events/create/", event_views.event_create, name="event-create"),
    path("events/speakers/", event_views.speaker_list, name="event-speaker-list"),
    path("events/speakers/create/", event_views.speaker_create, name="event-speaker-create"),
    path("events/<uuid:event_id>/", event_views.event_detail, name="event-detail"),
    path("events/<uuid:event_id>/update/", event_views.event_update, name="event-update"),
    path("events/<uuid:event_id>/publish/", event_views.event_publish, name="event-publish"),
    path("events/<uuid:event_id>/cancel/", event_views.event_cancel, name="event-cancel"),
    path("events/<uuid:event_id>/complete/", event_views.event_complete, name="event-complete"),
    path("events/<uuid:event_id>/banner/", event_views.event_banner, name="event-banner"),
    path(
        "events/<uuid:event_id>/registrants/",
        event_registrants_view := event_views.event_registrants,
        name="event-registrants",
    ),
    path(
        "events/<uuid:event_id>/attendance/",
        event_views.event_attendance,
        name="event-attendance",
    ),
    path("events/<uuid:event_id>/message/", event_views.event_message, name="event-message"),
    path("events/<uuid:event_id>/reminders/", event_views.event_reminders, name="event-reminders"),
    path("events/identities/", views.event_identity_list, name="event-identity-list"),
    path(
        "events/identities/<uuid:event_id>/",
        views.event_identity_detail,
        name="event-identity-detail",
    ),
    path(
        "events/<int:event_id>/qna/",
        qna_views.event_qna_detail,
        name="event-qna-detail",
    ),
    path(
        "events/<int:event_id>/qna/update/",
        qna_views.event_qna_update,
        name="event-qna-update",
    ),
    path(
        "events/<int:event_id>/qna/questions/<str:question_id>/",
        qna_views.event_qna_moderate,
        name="event-qna-moderate",
    ),
    path(
        "events/<int:event_id>/qna/retry/",
        qna_views.event_qna_retry,
        name="event-qna-retry",
    ),
    path(
        "events/<int:event_id>/qna/cohosts/",
        qna_views.event_qna_cohost,
        name="event-qna-cohost",
    ),
    path(
        "events/<int:event_id>/qna/cohosts/<str:invite_id>/revoke/",
        qna_views.event_qna_cohost_revoke,
        name="event-qna-cohost-revoke",
    ),
]
