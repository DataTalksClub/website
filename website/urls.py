from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from core import views as core_views

urlpatterns = [
    path("unified/", core_views.home, name="home"),
    path("health/live", core_views.liveness, name="health-live"),
    path("health/ready", core_views.readiness, name="health-ready"),
    path("studio/", include("studio.urls")),
    path("api/v1/admin/", include("website.admin_api_urls")),
    path("admin/", include("loginas.urls")),
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("accounts/", include("allauth.urls")),
    path("auth/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("api/", include("api.urls")),
    path("cadmin/", include("cadmin.urls")),
    path("", include("courses.urls")),
]
