from django.urls import include, path

urlpatterns = [
    path(
        "studio/_fixtures/credentials/",
        include("management_api.test_fixtures.studio_urls", namespace="studio"),
    ),
    path("api/v1/admin/", include("management_api.test_fixtures.urls")),
    # The credential fixture pages render on the Studio shell, and design system gave
    # that shell a theme toggle that reverses the account-side
    # ``toggle_dark_mode`` route.  Without the accounts URLconf mounted here the
    # fixture page 500s on ``NoReverseMatch`` the moment a browser signs in, so
    # the shell's own routes travel with the fixture isolation (issue #193).
    path("accounts/", include("accounts.urls")),
]
