"""CI-triggered editorial content sync, authenticated by GitHub Actions OIDC.

`.github/workflows/content-sync.yml` in this repository is the only caller.  It
requests a short-lived GitHub Actions OIDC token for the audience in
``CONTENT_SYNC_CI["audience"]`` and presents it as a bearer token.  The token is
verified against GitHub's published signing keys and must name this repository
(by immutable id as well as by name), the content-sync workflow file on the
default branch, and an allowed trigger.  Nothing is shared between GitHub and
production: there is no static secret to store, rotate or leak.

``GET`` reports the source's sync state and its latest sync log, so the
workflow can skip a run when the source repository's HEAD is already
synchronised and can wait for the outcome of a run it started.  ``POST`` starts
``community_base.content_sync``'s ``sync_content_source`` for one declared
source and answers 202 at once.  The engine's per-source lock keeps concurrent
runs from overlapping.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import UTC
from functools import lru_cache
from typing import Any

import jwt
from community_base.content_sync.models import ContentSource, SyncLog
from community_base.content_sync.orchestration import sync_content_source
from django.conf import settings
from django.db import connections
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger(__name__)

GITHUB_OIDC_ISSUER = "https://token.actions.githubusercontent.com"
GITHUB_OIDC_JWKS_URL = f"{GITHUB_OIDC_ISSUER}/.well-known/jwks"
MAX_REQUEST_BYTES = 4096
CLOCK_LEEWAY_SECONDS = 30


class CITokenError(Exception):
    """The bearer token is absent, invalid, or not from the allowed workflow."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _config() -> dict[str, Any]:
    return settings.CONTENT_SYNC_CI


@lru_cache(maxsize=1)
def _jwks_client() -> jwt.PyJWKClient:
    # PyJWKClient caches the key set and refetches it when an unknown key id
    # appears, so GitHub's key rotation needs no deploy.
    return jwt.PyJWKClient(GITHUB_OIDC_JWKS_URL, cache_keys=True, lifespan=3600, timeout=10)


def signing_key(token: str):
    return _jwks_client().get_signing_key_from_jwt(token).key


def verify_token(token: str) -> dict[str, Any]:
    """Return the verified claims of a GitHub Actions OIDC token, or raise."""

    config = _config()
    try:
        claims = jwt.decode(
            token,
            signing_key(token),
            algorithms=["RS256"],
            audience=config["audience"],
            issuer=GITHUB_OIDC_ISSUER,
            leeway=CLOCK_LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.PyJWKClientError as error:
        raise CITokenError("signing_key_unavailable") from error
    except jwt.InvalidTokenError as error:
        raise CITokenError("invalid_token") from error

    repository = config["repository"]
    expected = {
        "repository": repository,
        "repository_id": str(config["repository_id"]),
        "ref": config["ref"],
        "workflow_ref": f"{repository}/{config['workflow_path']}@{config['ref']}",
    }
    for claim, value in expected.items():
        if str(claims.get(claim, "")) != value:
            raise CITokenError(f"unexpected_{claim}")
    if claims.get("event_name") not in config["events"]:
        raise CITokenError("unexpected_event_name")
    return claims


def _bearer(request: HttpRequest) -> str:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise CITokenError("missing_token")
    return token.strip()


def _error(code: str, status: int) -> JsonResponse:
    return JsonResponse({"error": code}, status=status)


def _iso(value) -> str | None:
    # Fixed width UTC, so the workflow can order timestamps as strings.
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ") if value else None


def _state(source: ContentSource) -> dict[str, Any]:
    latest = SyncLog.objects.filter(source=source).order_by("-started_at").first()
    return {
        "source": source.slug,
        "repository": source.repo_name,
        "enabled": source.is_enabled,
        "last_sync_status": source.last_sync_status or None,
        "last_synced_commit": source.last_synced_commit or None,
        "last_synced_at": _iso(source.last_synced_at),
        "latest_sync": None
        if latest is None
        else {
            "status": latest.status,
            "commit": latest.commit_sha or None,
            "started_at": _iso(latest.started_at),
            "finished_at": _iso(latest.finished_at),
            "created": latest.items_created,
            "updated": latest.items_updated,
            "unchanged": latest.items_unchanged,
            "deleted": latest.items_deleted,
            "errors": list(latest.errors or [])[:20],
            "warnings": list(latest.warnings or [])[:20],
        },
    }


def _request_options(request: HttpRequest) -> tuple[str, bool]:
    if request.method == "GET":
        return request.GET.get("source", ""), False
    if len(request.body) > MAX_REQUEST_BYTES:
        raise ValueError("payload_too_large")
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError as error:
        raise ValueError("invalid_payload") from error
    if not isinstance(payload, dict):
        raise ValueError("invalid_payload")
    force = payload.get("force", False)
    if not isinstance(force, bool):
        raise ValueError("invalid_payload")
    return str(payload.get("source", "")), force


@csrf_exempt
def content_sync(request: HttpRequest) -> JsonResponse:
    if request.method not in {"GET", "POST"}:
        return _error("method_not_allowed", 405)
    try:
        claims = verify_token(_bearer(request))
    except CITokenError as error:
        logger.warning("content sync CI trigger refused: %s", error.code)
        return _error(error.code, 401)
    try:
        slug, force = _request_options(request)
    except ValueError as error:
        return _error(str(error), 400)
    if slug not in _config()["sources"]:
        return _error("source_not_allowed", 400)
    source = ContentSource.objects.filter(slug=slug).first()
    if source is None:
        return _error("source_not_found", 404)
    if request.method == "GET":
        return JsonResponse(_state(source))

    logger.info(
        "content sync CI trigger: source=%s force=%s run_id=%s event=%s",
        slug,
        force,
        claims.get("run_id", ""),
        claims.get("event_name", ""),
    )
    requested_at = timezone.now()
    start_sync(source.pk, force)
    return JsonResponse({**_state(source), "requested_at": _iso(requested_at)}, status=202)


def start_sync(source_id, force: bool) -> None:
    """Run the sync off the request thread.

    A full sync takes about a minute, longer than the CDN in front of the site
    waits for an origin response, so the request only starts it; the workflow
    polls ``GET`` for the resulting sync log.  If the process stops mid-sync,
    the engine's source lock expires and the next scheduled run picks it up.
    """

    threading.Thread(
        target=run_sync, args=(source_id, force), name="content-sync-ci", daemon=True
    ).start()


def run_sync(source_id, force: bool) -> None:
    try:
        sync_content_source(ContentSource.objects.get(pk=source_id), force=force)
    except Exception:
        logger.exception("content sync CI trigger: sync raised")
    finally:
        # Connections are per thread; this one must not outlive the thread.
        connections.close_all()
