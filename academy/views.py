import re

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.views.decorators.http import require_safe

from .models import PublishedResource


@require_safe
def resource(request: HttpRequest, path: str = "") -> HttpResponse:
    # Exact published keys only: no filesystem, upstream requests or fallback.
    if any(part in {".", ".."} for part in path.split("/")):
        raise Http404
    row = get_object_or_404(PublishedResource, path=path)
    body = bytes(row.content)
    if row.content_type.startswith("text/html"):
        # Replace the source auto-tracker with the site-owned consent and
        # payload boundary. Never send confirmation tokens to analytics.
        html = body.decode("utf-8")
        html = re.sub(
            r'<script\b[^>]*data-website-id="[^"]*"[^>]*></script>',
            "",
            html,
        )
        controls = render_to_string("academy/analytics.html", request=request)
        body = html.replace("</body>", controls + "</body>").encode("utf-8")
    response = HttpResponse(body, content_type=row.content_type)
    response["X-Content-Type-Options"] = "nosniff"
    # The signup/confirmation client talks directly to the existing backend.
    # This policy is scoped to Academy and grants no wildcard network access.
    response["Content-Security-Policy"] = (
        "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; "
        "form-action 'self'; script-src 'self' 'unsafe-inline' "
        "https://umami.169.58.119.163.sslip.io; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; font-src 'self' data:; connect-src 'self' "
        "https://augldvp3omxdxz3irqsmmxc25e0blpxx.lambda-url.eu-west-1.on.aws/signup "
        "https://augldvp3omxdxz3irqsmmxc25e0blpxx.lambda-url.eu-west-1.on.aws/confirm "
        "https://umami.169.58.119.163.sslip.io; "
        "frame-src https://www.youtube.com https://www.youtube-nocookie.com"
    )
    # Confirmation queries carry tokens; always keep Academy out of shared caches.
    response["Cache-Control"] = "private, no-store"
    return response
