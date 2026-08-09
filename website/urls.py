from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from accounts import api as account_api
from accounts.views.continuity import explicit_reauthentication
from content import public_views, review_views
from core import views as core_views
from courses import urls as course_urls

legacy_course_patterns = [
    pattern for pattern in course_urls.urlpatterns if pattern.name != "course_list"
]

urlpatterns = [
    path("robots.txt", core_views.robots, name="development-robots"),
    path("sitemap.xml", core_views.sitemap, name="development-sitemap"),
    path(
        "sitemaps/<slug:section>.xml",
        public_views.section_sitemap,
        name="section-sitemap",
    ),
    path("", core_views.home, name="home"),
    path(
        "courses/ai-dev-tools-zoomcamp",
        review_views.course_family,
        name="course-family-ai-dev-tools",
    ),
    path("", include("content.public_urls")),
    path("unified/", core_views.home, name="unified-home"),
    path("docs/", review_views.docs_home, name="docs-home"),
    path(
        "docs/courses/ai-dev-tools-zoomcamp/getting-started/",
        review_views.docs_getting_started,
        name="docs-ai-dev-tools-getting-started",
    ),
    path("faq/", review_views.faq_home, name="faq-home"),
    path(
        "faq/ai-dev-tools-zoomcamp.html",
        review_views.faq_ai_dev_tools,
        name="faq-ai-dev-tools",
    ),
    path("slack.html", review_views.slack, name="slack"),
    path("health/live", core_views.liveness, name="health-live"),
    path("health/ready", core_views.readiness, name="health-ready"),
    path("studio", core_views.management_slash_redirect, name="studio-slash-redirect"),
    path("studio/", include("studio.urls")),
    path("api/v1/admin/", include("website.admin_api_urls")),
    path("admin", core_views.management_slash_redirect, name="admin-slash-redirect"),
    path("admin/", include("loginas.urls")),
    path("admin/", admin.site.urls),
    path(
        "accounts/continue/",
        explicit_reauthentication,
        name="account_explicit_reauthentication",
    ),
    path("accounts/", include("accounts.urls")),
    path("accounts/", include("allauth.urls")),
    path("auth/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path(
        "api/v1/account/identity/",
        account_api.current_account_identity,
        name="account-identity",
    ),
    path(
        "api/account/identity/",
        account_api.compatibility_account_identity,
        name="compatibility-account-identity",
    ),
    path("api/", include("api.urls")),
    path("cadmin/", include("cadmin.urls")),
    path(
        "courses/ai-dev-tools-zoomcamp/cohorts/ai-dev-tools-2026",
        review_views.course_cohort,
        name="course-cohort-ai-dev-tools-2026",
    ),
    path(
        "courses/ai-dev-tools-zoomcamp/cohorts/ai-dev-tools-2026/registration-preview/",
        review_views.registration_preview,
        name="course-registration-preview-ai-dev-tools-2026",
    ),
    path(
        "courses/",
        include((course_urls.urlpatterns, "courses"), namespace="courses"),
    ),
    path("", include(legacy_course_patterns)),
]
