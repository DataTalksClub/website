from django.urls import path

from . import views

app_name = "studio"

urlpatterns = [
    path("", views.home, name="home"),
    path("audit/", views.audit_list, name="audit-list"),
    path("audit/<uuid:event_id>/", views.audit_detail, name="audit-detail"),
]
