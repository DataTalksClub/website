from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from core import views as core_views
from courses import urls as course_urls
from courses.views.course_list import course_list as course_list_view

legacy_course_patterns = [
    pattern for pattern in course_urls.urlpatterns if pattern.name != "course_list"
]

urlpatterns = [
    path("robots.txt", core_views.robots, name="development-robots"),
    path("sitemap.xml", core_views.sitemap, name="development-sitemap"),
    path("", core_views.home, name="home"),
    path("unified/", core_views.home, name="unified-home"),
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
    path("courses/", course_list_view, name="course_list"),
    path(
        "courses/",
        include((course_urls.urlpatterns, "courses"), namespace="courses"),
    ),
    path("", include(legacy_course_patterns)),
]
