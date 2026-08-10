from django.http import JsonResponse
from django.shortcuts import render
from django.urls import include, path

from website.urls import urlpatterns as website_urlpatterns


def synthetic_identity_conflict(request):
    return render(
        request,
        "socialaccount/identity_conflict.html",
        {"reason": "synthetic_accessibility_conflict"},
        status=409,
    )


def synthetic_missing_media(request):
    return render(
        request,
        "public/article_detail.html",
        {
            "seo_title": "Synthetic missing media — DataTalks.Club",
            "record": {
                "title": "Synthetic missing media",
                "published": "2026-08-10T12:00:00+00:00",
                "author_profiles": (),
                "subtitle": "The page remains meaningful when artwork is unavailable.",
                "media_available": False,
                "blocks": ({"kind": "paragraph", "text": "Synthetic public fixture."},),
            },
        },
    )


def synthetic_email_preferences(request):
    if request.method == "POST":
        return JsonResponse(
            {
                "field": request.POST.get("field", ""),
                "value": request.POST.get("value") == "true",
                "datamailer_synced": True,
            }
        )
    return JsonResponse(
        {
            "preferences": {
                "email_submission_confirmations": False,
                "email_deadline_reminders": False,
                "email_course_updates": False,
            }
        }
    )


urlpatterns = [
    path(
        "accounts/settings/email-preferences/",
        synthetic_email_preferences,
        name="accessibility-email-preferences",
    ),
    path(
        "_accessibility/identity-conflict/",
        synthetic_identity_conflict,
        name="accessibility-identity-conflict",
    ),
    path(
        "_accessibility/missing-media/",
        synthetic_missing_media,
        name="accessibility-missing-media",
    ),
    path(
        "studio/_fixtures/credentials/",
        include("management_api.test_fixtures.studio_urls", namespace="accessibility-studio"),
    ),
    *website_urlpatterns,
]
