from django.shortcuts import render
from django.urls import path

from website.urls import urlpatterns as website_urlpatterns


def synthetic_identity_conflict(request):
    return render(
        request,
        "socialaccount/identity_conflict.html",
        {"reason": "synthetic_browser_conflict"},
        status=409,
    )


urlpatterns = [
    path(
        "_identity-fixtures/link-conflict/",
        synthetic_identity_conflict,
        name="synthetic-identity-conflict",
    ),
    *website_urlpatterns,
]
