from django.urls import path
from django.views.decorators.csrf import csrf_exempt

from slack_access import admin_api as slack_api

from . import certificate_views, event_views, launch_views, peer_review_views, views


@csrf_exempt
def slack_member_ban(request, *args, **kwargs):
    if request.method in {"GET", "HEAD"}:
        return slack_api.member_preview(request, *args, **kwargs)
    return slack_api.member_ban(request, *args, **kwargs)


slack_member_ban.management_capability_keys = (  # type: ignore[attr-defined]
    "slack.members.preview",
    "slack.members.ban",
)
slack_member_ban.management_capability_views = {  # type: ignore[attr-defined]
    "GET": slack_api.member_preview,
    "POST": slack_api.member_ban,
}


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
def course_certificate_template(request, *args, **kwargs):
    if request.method in {"GET", "HEAD"}:
        return certificate_views.course_certificate_template_read(request, *args, **kwargs)
    return certificate_views.course_certificate_template_write(request, *args, **kwargs)


course_certificate_template.management_capability_keys = (  # type: ignore[attr-defined]
    "courses.certificate_template.read",
    "courses.certificate_template.write",
)
course_certificate_template.management_capability_views = {  # type: ignore[attr-defined]
    "GET": certificate_views.course_certificate_template_read,
    "PUT": certificate_views.course_certificate_template_write,
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


@csrf_exempt
def events_collection(request, *args, **kwargs):
    if request.method in {"GET", "HEAD"}:
        return event_views.event_list(request, *args, **kwargs)
    return event_views.event_create(request, *args, **kwargs)


events_collection.management_capability_keys = (  # type: ignore[attr-defined]
    "events.manage.list",
    "events.manage.create",
)
events_collection.management_capability_views = {  # type: ignore[attr-defined]
    "GET": event_views.event_list,
    "POST": event_views.event_create,
}


@csrf_exempt
def event_item(request, *args, **kwargs):
    if request.method in {"GET", "HEAD"}:
        return event_views.event_detail(request, *args, **kwargs)
    return event_views.event_update(request, *args, **kwargs)


event_item.management_capability_keys = (  # type: ignore[attr-defined]
    "events.manage.detail",
    "events.manage.update",
)
event_item.management_capability_views = {  # type: ignore[attr-defined]
    "GET": event_views.event_detail,
    "PATCH": event_views.event_update,
}


@csrf_exempt
def speakers_collection(request, *args, **kwargs):
    if request.method in {"GET", "HEAD"}:
        return event_views.speaker_list(request, *args, **kwargs)
    return event_views.speaker_create(request, *args, **kwargs)


speakers_collection.management_capability_keys = (  # type: ignore[attr-defined]
    "events.manage.speakers.list",
    "events.manage.speakers.create",
)
speakers_collection.management_capability_views = {  # type: ignore[attr-defined]
    "GET": event_views.speaker_list,
    "POST": event_views.speaker_create,
}


urlpatterns = [
    path("slack/applications", slack_api.application_list, name="admin-slack-applications"),
    path(
        "slack/applications/<uuid:application_id>/review",
        slack_api.application_review,
        name="admin-slack-review",
    ),
    path("slack/members/<uuid:member_id>/ban", slack_member_ban, name="admin-slack-ban"),
    path("slack/joins", slack_api.join_report, name="admin-slack-joins"),
    path("events", events_collection, name="admin-event-list"),
    path("events/speakers", speakers_collection, name="admin-event-speaker-list"),
    path("events/<uuid:event_id>", event_item, name="admin-event-detail"),
    path("events/<uuid:event_id>/publish", event_views.event_publish, name="admin-event-publish"),
    path("events/<uuid:event_id>/cancel", event_views.event_cancel, name="admin-event-cancel"),
    path(
        "events/<uuid:event_id>/complete", event_views.event_complete, name="admin-event-complete"
    ),
    path("events/<uuid:event_id>/banner", event_views.event_banner, name="admin-event-banner"),
    path(
        "events/<uuid:event_id>/registrants",
        event_views.event_registrants,
        name="admin-event-registrants",
    ),
    path(
        "events/<uuid:event_id>/attendance",
        event_views.event_attendance,
        name="admin-event-attendance",
    ),
    path("events/<uuid:event_id>/message", event_views.event_message, name="admin-event-message"),
    path(
        "events/<uuid:event_id>/reminders",
        event_views.event_reminders,
        name="admin-event-reminders",
    ),
    path("course-launch", launch_views.launch_read, name="course-launch-read"),
    path("course-launch/commands", launch_views.launch_write, name="course-launch-write"),
    path("course-launch/dispatch", launch_views.launch_send, name="course-launch-send"),
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
        "events/<int:event_id>/qna",
        event_qna_collection,
        name="admin-event-qna-read",
    ),
    path(
        "events/<int:event_id>/qna/questions/<str:question_id>",
        views.event_qna_moderate,
        name="admin-event-qna-moderate",
    ),
    path(
        "events/<int:event_id>/qna/retry",
        views.event_qna_retry,
        name="admin-event-qna-retry",
    ),
    path(
        "events/<int:event_id>/qna/cohosts",
        views.event_qna_cohost_create,
        name="admin-event-qna-cohost-create",
    ),
    path(
        "events/<int:event_id>/qna/cohosts/<str:invite_id>",
        views.event_qna_cohost_revoke,
        name="admin-event-qna-cohost-revoke",
    ),
    path(
        "courses/<slug:slug>/peer-reviews",
        csrf_exempt(peer_review_views.course_peer_reviews),
        name="admin-course-peer-reviews",
    ),
    path(
        "courses/<slug:slug>/peer-reviews/assignments",
        csrf_exempt(peer_review_views.course_peer_review_assignments),
        name="admin-course-peer-review-assignments",
    ),
    path(
        "courses/<slug:slug>/certificate-template",
        course_certificate_template,
        name="admin-course-certificate-template",
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
