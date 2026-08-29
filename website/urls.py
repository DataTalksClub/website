from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from accounts import api as account_api
from accounts.views.continuity import explicit_reauthentication
from cadmin.legacy_urls import legacy_course_list_redirect
from content import public_views, review_views
from core import views as core_views
from courses import urls as course_urls
from courses.views import course_aliases, course_list
from studio_courses import urls as studio_course_urls

course_patterns = [
    pattern
    for pattern in course_urls.urlpatterns
    if pattern.name != "course_list" and str(pattern.pattern) != "<slug:course_slug>/"
]
namespaced_course_patterns = [*course_patterns]
namespaced_course_patterns.append(
    path(
        "<slug:course_slug>/",
        course_aliases.legacy_course_redirect,
        name="course",
    )
)

urlpatterns = [
    path("robots.txt", core_views.robots, name="development-robots"),
    path("sitemap.xml", core_views.sitemap, name="development-sitemap"),
    path(
        "sitemaps/<slug:section>.xml",
        public_views.section_sitemap,
        name="section-sitemap",
    ),
    path("", core_views.home, name="home"),
    path("sponsors", core_views.sponsors, name="sponsors"),
    path("", include("content.public_urls")),
    path("unified/", core_views.home, name="unified-home"),
    path("docs/", review_views.docs_home, name="docs-home"),
    path("docs", public_views.permanent_public_redirect, {"target": "/docs/"}),
    path(
        "docs/courses/ai-dev-tools-zoomcamp/getting-started/",
        review_views.docs_getting_started,
        name="docs-ai-dev-tools-getting-started",
    ),
    path("docs/assets/<path:asset>", review_views.docs_asset, name="docs-asset"),
    path("docs/<path:doc_path>", review_views.docs_page, name="docs-page"),
    path("faq/", review_views.faq_home, name="faq-home"),
    path("faq", public_views.permanent_public_redirect, {"target": "/faq/"}),
    path("faq/json/courses.json", review_views.faq_courses_json, name="faq-courses-json"),
    path(
        "faq/json/<slug:course_slug>.json",
        review_views.faq_course_json,
        name="faq-course-json",
    ),
    path(
        "faq/images/<slug:course_slug>/<path:asset>",
        review_views.faq_asset,
        name="faq-image",
    ),
    path(
        "faq/assets/<slug:course_slug>/<path:asset>",
        review_views.faq_asset,
        name="faq-asset",
    ),
    path(
        "faq/ai-dev-tools-zoomcamp.html",
        review_views.faq_ai_dev_tools,
        name="faq-ai-dev-tools",
    ),
    path("faq/<slug:course_slug>.html", review_views.faq_course, name="faq-course"),
    path("slack", review_views.slack, name="slack"),
    path("slack.html", public_views.permanent_public_redirect, {"target": "/slack"}),
    path(
        "slack/guidelines.html",
        public_views.permanent_public_redirect,
        {"target": "/slack"},
    ),
    path("health/live", core_views.liveness, name="health-live"),
    path("health/ready", core_views.readiness, name="health-ready"),
    path("studio", core_views.management_slash_redirect, name="studio-slash-redirect"),
    path("studio/", include("studio.urls")),
    studio_course_urls.canonical_root_pattern("studio/courses"),
    # The exact copied CMP shell still emits this route name for its staff menu.  Keep the
    # compatibility name pointed at Studio while the template provenance remains byte-for-byte.
    path(
        "studio/courses",
        studio_course_urls.ROUTE_DEFINITIONS[0][1],
        name="cadmin_course_list",
    ),
    path("studio/courses/", studio_course_urls.course_list_slash_redirect),
    path("studio/courses/", include(studio_course_urls.child_urlpatterns)),
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
    path(
        "cadmin",
        legacy_course_list_redirect,
        name="legacy-studio-courses-root",
    ),
    path("cadmin/", include("cadmin.legacy_urls")),
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
    path("courses", course_list.course_list, name="course_list"),
    path(
        "courses/",
        public_views.permanent_public_redirect,
        {"target": "/courses"},
        name="course-list-slash-redirect",
    ),
    path(
        "courses/",
        include(course_patterns),
    ),
    path(
        "courses/",
        include((namespaced_course_patterns, "courses"), namespace="courses"),
    ),
    path(
        "<slug:course_slug>/",
        course_aliases.legacy_course_redirect,
        name="legacy-course",
    ),
]
