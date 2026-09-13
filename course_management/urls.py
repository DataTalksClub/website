from content import review_views
from django.contrib import admin
from django.urls import include, path

from studio_courses import urls as studio_course_urls

loginas_urls = include("loginas.urls")
accounts_urls = include("accounts.urls")
allauth_urls = include("allauth.urls")
api_urls = include("api.urls")
courses_urls = include("courses.urls")

urlpatterns = [
    path("admin/", loginas_urls),
    path("admin/", admin.site.urls),

    path("accounts/", accounts_urls),
    path("accounts/", allauth_urls),

    path("api/", api_urls),
    studio_course_urls.canonical_root_pattern("studio/courses"),
    path("studio/courses/", studio_course_urls.course_list_slash_redirect),
    path("studio/courses/", include(studio_course_urls.child_urlpatterns)),
    path("cadmin/", include("cadmin.legacy_urls")),
    # The shared public templates reverse the community page by name, so the
    # copied root carries the same /slack route the current root publishes.
    path("slack", review_views.slack, name="slack"),
    path("", courses_urls),
]
