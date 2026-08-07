from django.contrib import admin
from django.urls import include, path

from core import views as core_views

urlpatterns = [
    path("", core_views.home, name="home"),
    path("health/live", core_views.liveness, name="health-live"),
    path("health/ready", core_views.readiness, name="health-ready"),
    path("accounts/", include("django.contrib.auth.urls")),
    path("studio/", include("studio.urls")),
    path("api/v1/admin/", include("api.urls")),
    path("django-admin/", admin.site.urls),
]
