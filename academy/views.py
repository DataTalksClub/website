from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse, HttpResponsePermanentRedirect
from django.shortcuts import render
from django.views.decorators.http import require_safe

from core.middleware import SECURITY_HEADERS
from core.runtime_config import get_str_setting

_ICS_DIR = Path(__file__).resolve().parent / "ics"
_ALLOWED_ICS = frozenset({"hireable-with-ai", "session-1", "session-2", "session-3"})
_DEFAULT_UMAMI_SCRIPT_ORIGINS = ("https://cloud.umami.is",)
_DEFAULT_UMAMI_CONNECT_ORIGINS = ("https://cloud.umami.is", "https://api-gateway.umami.dev")


def _origin(url: str) -> str:
    if not url:
        return ""
    parsed = urlsplit(url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return ""


def _extend_directive(policy: str, directive: str, extra_sources: tuple[str, ...]) -> str:
    additions = [src for src in dict.fromkeys(extra_sources) if src]
    if not additions:
        return policy
    parts = [part.strip() for part in policy.split(";") if part.strip()]
    updated: list[str] = []
    for part in parts:
        tokens = part.split()
        if tokens and tokens[0] == directive:
            existing = set(tokens[1:])
            for src in additions:
                if src not in existing:
                    tokens.append(src)
                    existing.add(src)
            updated.append(" ".join(tokens))
        else:
            updated.append(part)
    return "; ".join(updated)


def _academy_csp() -> str:
    api_origin = _origin(getattr(settings, "ACADEMY_API_URL", ""))
    umami_origin = _origin(getattr(settings, "ACADEMY_UMAMI_SCRIPT_URL", ""))
    script_extras = (*_DEFAULT_UMAMI_SCRIPT_ORIGINS, umami_origin)
    connect_extras = (api_origin, *_DEFAULT_UMAMI_CONNECT_ORIGINS, umami_origin)
    policy = SECURITY_HEADERS["Content-Security-Policy"]
    policy = _extend_directive(policy, "script-src", script_extras)
    policy = _extend_directive(policy, "connect-src", connect_extras)
    return policy


def _academy_context(path: str) -> dict[str, str]:
    canonical_origin = get_str_setting("site.origin.canonical")
    return {
        "canonical_origin": canonical_origin,
        "canonical_url": f"{canonical_origin}{path}",
        "academy_api_url": getattr(settings, "ACADEMY_API_URL", ""),
        "umami_script_url": getattr(settings, "ACADEMY_UMAMI_SCRIPT_URL", ""),
        "umami_website_id": getattr(settings, "ACADEMY_UMAMI_WEBSITE_ID", ""),
    }


def _render_academy(request: HttpRequest, template_name: str, path: str) -> HttpResponse:
    response = render(request, template_name, _academy_context(path))
    response["Content-Security-Policy"] = _academy_csp()
    return response


@require_safe
def home(request: HttpRequest) -> HttpResponse:
    return _render_academy(request, "academy/home.html", "/academy/")


@require_safe
def agentic_engineering(request: HttpRequest) -> HttpResponse:
    return _render_academy(
        request, "academy/agentic_engineering.html", "/academy/agentic-engineering/"
    )


@require_safe
def ai_engineering(request: HttpRequest) -> HttpResponse:
    return _render_academy(request, "academy/ai_engineering.html", "/academy/ai-engineering/")


@require_safe
def sessions(request: HttpRequest) -> HttpResponse:
    return _render_academy(request, "academy/sessions.html", "/academy/sessions/")


@require_safe
def sessions_materials(request: HttpRequest) -> HttpResponse:
    return _render_academy(
        request, "academy/sessions_materials.html", "/academy/sessions/materials/"
    )


@require_safe
def confirm(request: HttpRequest) -> HttpResponse:
    return _render_academy(request, "academy/confirm.html", "/academy/confirm/")


@require_safe
def session_ics(request: HttpRequest, slug: str) -> HttpResponse:
    del request
    if slug not in _ALLOWED_ICS:
        raise Http404
    ics_path = _ICS_DIR / f"{slug}.ics"
    if not ics_path.is_file():
        raise Http404
    return HttpResponse(
        ics_path.read_bytes(),
        content_type="text/calendar; charset=utf-8",
    )


@require_safe
def layoff_proof_redirect(request: HttpRequest) -> HttpResponsePermanentRedirect:
    query = request.META.get("QUERY_STRING", "")
    target = "/academy/sessions/"
    if query:
        target = f"{target}?{query}"
    return HttpResponsePermanentRedirect(target)
