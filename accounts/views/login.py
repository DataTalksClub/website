from urllib.parse import urlencode

from allauth.socialaccount.models import SocialApp
from allauth.socialaccount.providers import registry
from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.cache import cache
from django.shortcuts import render
from django.urls import reverse

from accounts.navigation import safe_next_path


async def social_login_view(request):
    safe_next = safe_next_path(request)
    providers = await get_available_providers(safe_next)
    context = {
        "providers": providers,
        "safe_next": safe_next,
    }
    render_async = sync_to_async(render)

    response = await render_async(
        request,
        "accounts/login.html",
        context,
    )
    return response


@sync_to_async
def get_available_providers(safe_next="/"):
    cached_providers = cache.get("available_providers")
    if cached_providers is not None:
        return _providers_with_next(cached_providers, safe_next)

    providers = []
    site_id = settings.SITE_ID
    provider_choices = registry.as_choices()
    for provider, name in provider_choices:
        provider_enabled = SocialApp.objects.filter(
            provider=provider,
            sites__id__exact=site_id,
        ).exists()
        if not provider_enabled:
            continue

        provider_record = {
            "name": name,
            "login_url": reverse(f"{provider}_login"),
        }
        providers.append(provider_record)

    cache.set("available_providers", providers, 60 * 60)
    return _providers_with_next(providers, safe_next)


def _providers_with_next(providers, safe_next):
    query = urlencode({"next": safe_next})
    return [
        {
            **provider,
            "login_url": f"{provider['login_url']}?{query}",
        }
        for provider in providers
    ]
