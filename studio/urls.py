from django.urls import path

from . import views

app_name = "studio"

urlpatterns = [
    path("", views.home, name="home"),
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
]
