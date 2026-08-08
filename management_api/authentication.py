from __future__ import annotations

from dataclasses import dataclass

from django.http import HttpRequest
from django.utils import timezone

from management_auth.models import APICredential, APIPrincipal
from management_auth.rate_limits import (
    RateLimitExceeded,
    RateLimitUnavailable,
    verify_with_adaptive_limit,
)
from management_auth.services import note_credential_used, principal_has_permission
from management_auth.tokens import PREFIX_PATTERN, dummy_verify, parse_token, verify_secret

from .errors import APIError, authentication_required, permission_denied

_TOKEN_INPUT_KEYS = frozenset({"access_token", "authorization", "bearer", "token"})


@dataclass(frozen=True, slots=True)
class APIIdentity:
    principal: APIPrincipal
    credential: APICredential


def _raise_rate(error: RateLimitExceeded | RateLimitUnavailable) -> None:
    retry_after = error.retry_after if isinstance(error, RateLimitExceeded) else 60
    raise APIError(
        429,
        "rate_limited",
        "The management API rate limit was reached.",
        headers={"Retry-After": str(retry_after)},
    ) from error


def _reject_alternate_sources(request: HttpRequest) -> None:
    if any(key.casefold() in _TOKEN_INPUT_KEYS for key in request.GET):
        raise authentication_required()
    if any(key.casefold() in _TOKEN_INPUT_KEYS for key in request.COOKIES):
        raise authentication_required()
    if request.content_type in {
        "application/x-www-form-urlencoded",
        "multipart/form-data",
    }:
        raise authentication_required()


def _dummy_rejected(secret: str) -> bool:
    dummy_verify(secret)
    return False


def authenticate(request: HttpRequest) -> APIIdentity:
    _reject_alternate_sources(request)
    header = request.META.get("HTTP_AUTHORIZATION", "")
    if not isinstance(header, str) or "," in header or not header.startswith("Bearer "):
        raise authentication_required()
    raw = header.removeprefix("Bearer ")
    if not raw or raw != raw.strip() or " " in raw:
        raise authentication_required()
    parsed = parse_token(raw)
    if parsed is None:
        raise authentication_required()

    credential = (
        APICredential.objects.select_related("principal", "principal__user")
        .filter(prefix=parsed.prefix)
        .first()
    )
    if credential is None:
        try:
            verify_with_adaptive_limit(
                prefix=(parsed.prefix if PREFIX_PATTERN.fullmatch(parsed.prefix) else "invalid"),
                verifier=lambda: _dummy_rejected(parsed.secret),
            )
        except (RateLimitExceeded, RateLimitUnavailable) as error:
            _raise_rate(error)
        raise authentication_required()

    try:
        verified = verify_with_adaptive_limit(
            prefix=parsed.prefix,
            verifier=lambda: verify_secret(parsed.secret, credential.secret_digest),
        )
    except (RateLimitExceeded, RateLimitUnavailable) as error:
        _raise_rate(error)
    if not verified:
        raise authentication_required()

    now = timezone.now()
    principal = credential.principal
    rotated_out = credential.rotated_at is not None and (
        credential.overlap_expires_at is None or credential.overlap_expires_at <= now
    )
    linked_user_disabled = principal.kind == APIPrincipal.Kind.HUMAN and (
        principal.user is None or not principal.user.is_active
    )
    if (
        credential.digest_algorithm != "pbkdf2_sha256"
        or credential.digest_version != 1
        or credential.expires_at <= now
        or credential.revoked_at is not None
        or rotated_out
        or not principal.is_active
        or linked_user_disabled
    ):
        raise authentication_required()
    return APIIdentity(principal=principal, credential=credential)


def authorize(identity: APIIdentity, capability) -> None:
    principal = identity.principal
    if capability.key not in identity.credential.scopes:
        raise permission_denied()
    if not principal_has_permission(principal, capability.django_permission):
        raise permission_denied()
    if capability.function_policy is not None:
        try:
            if capability.function_policy(principal, identity.credential) is not True:
                raise permission_denied()
        except APIError:
            raise
        except Exception as error:
            raise permission_denied() from error


def mark_used(identity: APIIdentity) -> None:
    note_credential_used(identity.credential)
