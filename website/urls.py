from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from accounts import api as account_api
from accounts.views.continuity import explicit_reauthentication
from content import review_views
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
    path("events.html", review_views.events, name="events"),
    path("articles.html", review_views.articles, name="articles"),
    path(
        "blog/ai-dev-tools-zoomcamp.html",
        review_views.article_detail,
        name="article-ai-dev-tools",
    ),
    path("podcast.html", review_views.podcast, name="podcast"),
    path(
        "podcast/s24e06-how-to-build-ai-that-actually-ships-in-production.html",
        review_views.podcast_detail,
        name="podcast-ai-production",
    ),
    path(
        "people/aleksandrkim.html",
        review_views.person_detail,
        name="person-aleksandr-kim",
    ),
    path("books.html", review_views.books, name="books"),
    path(
        "books/20250922-how-software-fails.html",
        review_views.book_detail,
        name="book-how-software-fails",
    ),
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
    path("podwiki/", review_views.podwiki_home, name="podwiki-home"),
    path(
        "podwiki/wiki/ai-coding-tools/",
        review_views.podwiki_detail,
        name="podwiki-ai-coding-tools",
    ),
    path("podwiki/search/", review_views.podwiki_search, name="podwiki-search"),
    path("slack.html", review_views.slack, name="slack"),
    path("health/live", core_views.liveness, name="health-live"),
    path("health/ready", core_views.readiness, name="health-ready"),
    path("studio/", include("studio.urls")),
    path("api/v1/admin/", include("website.admin_api_urls")),
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
        "courses/ai-dev-tools-zoomcamp/",
        review_views.course_family,
        name="course-family-ai-dev-tools",
    ),
    path(
        "courses/ai-dev-tools-zoomcamp/cohorts/ai-dev-tools-2026/",
        review_views.course_cohort,
        name="course-cohort-ai-dev-tools-2026",
    ),
    path(
        "courses/ai-dev-tools-zoomcamp/cohorts/ai-dev-tools-2026/registration-preview/",
        review_views.registration_preview,
        name="course-registration-preview-ai-dev-tools-2026",
    ),
    path("courses/", course_list_view, name="course_list"),
    path(
        "courses/",
        include((course_urls.urlpatterns, "courses"), namespace="courses"),
    ),
    path("", include(legacy_course_patterns)),
]
