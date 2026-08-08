from django.urls import include, path

urlpatterns = [
    path(
        "studio/_fixtures/credentials/",
        include("management_api.test_fixtures.studio_urls", namespace="studio"),
    ),
    path("api/v1/admin/", include("management_api.test_fixtures.urls")),
]
