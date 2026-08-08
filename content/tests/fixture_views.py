from __future__ import annotations

from pathlib import Path

from django.http import HttpRequest, HttpResponse
from django.template import Context, Engine

from content.queries import (
    ResolvePublicAsset,
    ResolvePublicDocument,
    resolve_public_asset,
    resolve_public_document,
)
from core.services import ServiceContext

_CONTEXT = ServiceContext(correlation_id="content-browser-fixture")
_TEMPLATE_DIRECTORY = Path(__file__).parent / "templates"
_ENGINE = Engine(dirs=[str(_TEMPLATE_DIRECTORY)], debug=False)


def fixture_document(_request: HttpRequest) -> HttpResponse:
    document = resolve_public_document(
        ResolvePublicDocument("/Fixture/Exact.html"),
        context=_CONTEXT,
    )
    if document is None:
        return HttpResponse("Fixture not found", status=404, content_type="text/plain")
    template = _ENGINE.get_template("content/fixture_release.html")
    return HttpResponse(
        template.render(Context({"document": document})),
        content_type="text/html; charset=utf-8",
    )


def fixture_asset(_request: HttpRequest) -> HttpResponse:
    asset = resolve_public_asset(
        ResolvePublicAsset("/assets/Fixture-Logo.svg"),
        context=_CONTEXT,
    )
    if asset is None:
        return HttpResponse("Fixture asset not found", status=404, content_type="text/plain")
    return HttpResponse(
        '<svg xmlns="http://www.w3.org/2000/svg" width="160" height="80" viewBox="0 0 160 80">'
        '<rect width="160" height="80" rx="12" fill="#172554"/>'
        '<circle cx="38" cy="40" r="20" fill="#38bdf8"/>'
        '<text x="70" y="48" font-family="sans-serif" font-size="24" fill="white">DTC</text>'
        "</svg>",
        content_type=asset.content_type,
    )


def not_found(_request: HttpRequest, exception: Exception) -> HttpResponse:
    del exception
    return HttpResponse("Content fixture not found", status=404, content_type="text/plain")
