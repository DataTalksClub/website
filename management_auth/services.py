from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from accounts.studio_authorization import has_explicit_permission
from core.audit import AuditWriteContext, record_audit_event
from core.capabilities import Capability
from core.idempotency import JsonObject, JsonValue
from core.models import AuditEvent, RevisionConflict
from core.redaction import is_sensitive_text

from .constants import (
    DEFAULT_CREDENTIAL_LIFETIME,
    DEFAULT_ROTATION_OVERLAP,
    DIGEST_ALGORITHM,
    DIGEST_VERSION,
    LAST_USED_WRITE_INTERVAL,
    MAX_CREDENTIAL_LIFETIME,
    MAX_ROTATION_OVERLAP,
    PREFIX_COLLISION_RETRIES,
)
from .idempotency import OneTimeCommandResult, execute_one_time_idempotent
from .models import APICredential, APIPrincipal
from .tokens import GeneratedToken, encode_secret, generate_token


class CredentialCreationFailed(RuntimeError):
    pass


class CredentialStateConflict(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class IssuedCredential:
    credential: APICredential
    raw_token: str


def _audit_context(principal: APIPrincipal) -> AuditWriteContext:
    return AuditWriteContext(
        api_principal_id=principal.id,
        actor_ref=f"api_principal:{principal.id}",
    )


def _audit_credential(
    *,
    actor_principal: APIPrincipal,
    credential: APICredential,
    action: str,
    changes: dict,
    using: str,
) -> None:
    record_audit_event(
        action=action,
        target_type="management.credential",
        target_id=credential.id,
        target_label=credential.name,
        outcome=AuditEvent.Outcome.SUCCEEDED,
        context=_audit_context(actor_principal),
        changes=changes,
        metadata={
            "credential_id": str(credential.id),
            "principal_id": str(credential.principal_id),
            "prefix": credential.prefix,
            "scopes": list(credential.scopes),
        },
        using=using,
    )


def _safe_name(value: str) -> str:
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > 120
        or any(ord(character) < 32 for character in normalized)
        or is_sensitive_text(normalized)
    ):
        raise ValueError("name must be safe bounded text")
    return normalized


def _safe_snapshot(value: str) -> str:
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > 128
        or any(ord(character) < 32 for character in normalized)
        or is_sensitive_text(normalized)
    ):
        raise ValueError("identity snapshot must be safe bounded text")
    return normalized


def create_principal(
    *,
    kind: str,
    name: str,
    identity_snapshot: str,
    user=None,
    created_by=None,
    permissions: Iterable[Permission] = (),
    using: str = "default",
) -> APIPrincipal:
    if kind not in APIPrincipal.Kind.values:
        raise ValueError("principal kind is invalid")
    if (kind == APIPrincipal.Kind.HUMAN) != (user is not None):
        raise ValueError("human principals require one user and service principals require none")
    with transaction.atomic(using=using):
        principal = APIPrincipal.objects.using(using).create(
            kind=kind,
            name=_safe_name(name),
            identity_snapshot=_safe_snapshot(identity_snapshot),
            user=user,
            created_by=created_by,
        )
        principal.permissions.set(tuple(permissions))
        return principal


def set_principal_active(
    *,
    principal_id: uuid.UUID,
    is_active: bool,
    expected_revision: int,
    using: str = "default",
) -> APIPrincipal:
    with transaction.atomic(using=using):
        principal = APIPrincipal.objects.using(using).get(pk=principal_id)
        if principal.revision != expected_revision:
            raise RevisionConflict(expected=expected_revision, actual=principal.revision)
        principal.is_active = is_active
        principal.revision += 1
        principal.save(using=using, update_fields=("is_active", "revision", "updated_at"))
        return principal


def replace_principal_permissions(
    *,
    principal_id: uuid.UUID,
    permissions: Iterable[Permission],
    expected_revision: int,
    using: str = "default",
) -> APIPrincipal:
    with transaction.atomic(using=using):
        principal = APIPrincipal.objects.using(using).get(pk=principal_id)
        if principal.revision != expected_revision:
            raise RevisionConflict(expected=expected_revision, actual=principal.revision)
        principal.revision += 1
        principal.save(using=using, update_fields=("revision", "updated_at"))
        principal.permissions.set(tuple(permissions))
        return principal


def principal_has_permission(principal: APIPrincipal, permission: str) -> bool:
    try:
        app_label, codename = permission.split(".", 1)
    except ValueError:
        return False
    if not principal.permissions.filter(
        content_type__app_label=app_label,
        codename=codename,
    ).exists():
        return False
    if principal.kind == APIPrincipal.Kind.SERVICE:
        return principal.user_id is None
    user = principal.user
    return bool(user is not None and user.is_active and has_explicit_permission(user, permission))


def _reload_linked_user(principal: APIPrincipal, *, using: str) -> None:
    """Recheck current human account state before a credential effect."""

    if principal.user_id is None:
        return
    principal.user = get_user_model().objects.using(using).get(pk=principal.user_id)


def lock_actor_authority(
    actor_principal: APIPrincipal,
    *,
    permission: str,
    using: str,
    actor_credential: APICredential | None = None,
    actor_capability: Capability | None = None,
) -> APIPrincipal:
    """Fence current actor authority in the same transaction as the effect.

    Studio calls authenticate with a session and therefore have no API
    credential to fence. Admin API calls must provide both the exact
    authenticated credential and capability so their mutable authority is
    locked and rechecked immediately before any effect.
    """

    if (actor_credential is None) != (actor_capability is None):
        raise PermissionError("actor credential authority is incomplete")

    try:
        actor = APIPrincipal.objects.using(using).select_related("user").get(pk=actor_principal.pk)
        _reload_linked_user(actor, using=using)
    except (APIPrincipal.DoesNotExist, get_user_model().DoesNotExist) as error:
        raise PermissionError("actor authority is unavailable") from error
    if not actor.is_active or not principal_has_permission(actor, permission):
        raise PermissionError("actor authority was denied")

    if actor_credential is not None and actor_capability is not None:
        from management_registry import CAPABILITY_REGISTRY

        capability = CAPABILITY_REGISTRY.get(actor_capability.key)
        if capability is None:
            raise PermissionError("actor capability authority is unavailable")
        try:
            credential = APICredential.objects.using(using).get(pk=actor_credential.pk)
        except APICredential.DoesNotExist as error:
            raise PermissionError("actor credential authority is unavailable") from error
        now = timezone.now()
        rotated_out = credential.rotated_at is not None and (
            credential.overlap_expires_at is None or credential.overlap_expires_at <= now
        )
        if (
            credential.principal_id != actor.id
            or credential.digest_algorithm != DIGEST_ALGORITHM
            or credential.digest_version != DIGEST_VERSION
            or credential.expires_at <= now
            or credential.revoked_at is not None
            or rotated_out
            or capability.django_permission != permission
            or capability.key not in credential.scopes
        ):
            raise PermissionError("actor credential authority was denied")
        if capability.function_policy is not None:
            try:
                allowed = capability.function_policy(actor, credential) is True
            except Exception as error:
                raise PermissionError("actor function policy was denied") from error
            if not allowed:
                raise PermissionError("actor function policy was denied")
    return actor


def normalize_scopes(scopes: Iterable[str], *, principal: APIPrincipal) -> tuple[str, ...]:
    from management_registry import CAPABILITY_REGISTRY

    normalized: set[str] = set()
    for value in scopes:
        if not isinstance(value, str) or not value or len(value) > 128:
            raise ValueError("credential scope is invalid")
        capability = CAPABILITY_REGISTRY.get(value)
        if capability is None:
            raise ValueError("credential scope is unknown")
        if not principal_has_permission(principal, capability.django_permission):
            raise PermissionError("credential scope exceeds principal authority")
        normalized.add(value)
    if not normalized or len(normalized) > 64:
        raise ValueError("credentials require between 1 and 64 scopes")
    return tuple(sorted(normalized))


def _validate_expiry(now, expires_at):
    selected = expires_at or now + DEFAULT_CREDENTIAL_LIFETIME
    if selected <= now or selected > now + MAX_CREDENTIAL_LIFETIME:
        raise ValueError("credential expiry must be within 90 days")
    return selected


def _validate_overlap(overlap: timedelta) -> timedelta:
    if overlap < DEFAULT_ROTATION_OVERLAP or overlap > MAX_ROTATION_OVERLAP:
        raise ValueError("rotation overlap must be between zero and one hour")
    return overlap


def _insert_generated_credential(
    *,
    principal: APIPrincipal,
    name: str,
    scopes: tuple[str, ...],
    expires_at,
    created_by,
    predecessor: APICredential | None,
    token_factory: Callable[[], GeneratedToken],
    using: str,
) -> IssuedCredential:
    for _attempt in range(PREFIX_COLLISION_RETRIES):
        generated = token_factory()
        try:
            with transaction.atomic(using=using):
                credential = APICredential.objects.using(using).create(
                    principal=principal,
                    name=name,
                    prefix=generated.prefix,
                    secret_digest=encode_secret(generated.secret),
                    digest_algorithm=DIGEST_ALGORITHM,
                    digest_version=DIGEST_VERSION,
                    scopes=list(scopes),
                    expires_at=expires_at,
                    predecessor=predecessor,
                    created_by=created_by,
                )
            return IssuedCredential(credential=credential, raw_token=generated.raw)
        except IntegrityError:
            if APICredential.objects.using(using).filter(prefix=generated.prefix).exists():
                continue
            raise
    raise CredentialCreationFailed("credential prefix allocation failed")


def _response(issued: IssuedCredential) -> OneTimeCommandResult:
    credential = issued.credential
    safe: JsonObject = {
        "credential_id": str(credential.id),
        "principal_id": str(credential.principal_id),
        "prefix": credential.prefix,
        "name": credential.name,
        "scopes": list(credential.scopes),
        "expires_at": credential.expires_at.isoformat(),
        "revision": credential.revision,
    }
    return OneTimeCommandResult(
        response={**safe, "token": issued.raw_token},
        safe_result=safe,
    )


def issue_credential_once(
    *,
    actor_principal: APIPrincipal,
    target_principal_id: uuid.UUID,
    name: str,
    scopes: Iterable[str],
    idempotency_key: str,
    actor_permission: str,
    actor_credential: APICredential | None = None,
    actor_capability: Capability | None = None,
    expires_at=None,
    created_by=None,
    token_factory: Callable[[], GeneratedToken] = generate_token,
    using: str = "default",
) -> OneTimeCommandResult:
    now = timezone.now()
    selected_name = _safe_name(name)
    requested_scopes = tuple(scopes)
    request_scopes: list[JsonValue] = list(sorted(requested_scopes))
    request: JsonObject = {
        "target_principal_id": str(target_principal_id),
        "name": selected_name,
        "scopes": request_scopes,
        "expires_at": expires_at.isoformat() if expires_at is not None else None,
    }

    def command() -> OneTimeCommandResult:
        actor = lock_actor_authority(
            actor_principal,
            permission=actor_permission,
            using=using,
            actor_credential=actor_credential,
            actor_capability=actor_capability,
        )
        target = APIPrincipal.objects.using(using).get(pk=target_principal_id)
        _reload_linked_user(target, using=using)
        if not target.is_active:
            raise CredentialStateConflict("target principal is inactive")
        normalized_scopes = normalize_scopes(requested_scopes, principal=target)
        issued = _insert_generated_credential(
            principal=target,
            name=selected_name,
            scopes=normalized_scopes,
            expires_at=_validate_expiry(now, expires_at),
            created_by=created_by,
            predecessor=None,
            token_factory=token_factory,
            using=using,
        )
        _audit_credential(
            actor_principal=actor,
            credential=issued.credential,
            action="management.credential.created",
            changes={"created": True},
            using=using,
        )
        return _response(issued)

    return execute_one_time_idempotent(
        principal=actor_principal,
        operation="management.credential.create",
        key=idempotency_key,
        request=request,
        command=command,
        using=using,
    )


def rotate_credential_once(
    *,
    actor_principal: APIPrincipal,
    credential_id: uuid.UUID,
    expected_revision: int,
    idempotency_key: str,
    actor_permission: str,
    actor_credential: APICredential | None = None,
    actor_capability: Capability | None = None,
    overlap: timedelta = DEFAULT_ROTATION_OVERLAP,
    expires_at=None,
    created_by=None,
    token_factory: Callable[[], GeneratedToken] = generate_token,
    using: str = "default",
) -> OneTimeCommandResult:
    selected_overlap = _validate_overlap(overlap)
    request = {
        "credential_id": str(credential_id),
        "expected_revision": expected_revision,
        "overlap_seconds": int(selected_overlap.total_seconds()),
        "expires_at": expires_at.isoformat() if expires_at is not None else None,
    }

    def command() -> OneTimeCommandResult:
        now = timezone.now()
        actor = lock_actor_authority(
            actor_principal,
            permission=actor_permission,
            using=using,
            actor_credential=actor_credential,
            actor_capability=actor_capability,
        )
        credential = (
            APICredential.objects.using(using).select_related("principal").get(pk=credential_id)
        )
        _reload_linked_user(credential.principal, using=using)
        if credential.revision != expected_revision:
            raise RevisionConflict(expected=expected_revision, actual=credential.revision)
        if credential.revoked_at is not None or credential.rotated_at is not None:
            raise CredentialStateConflict("credential cannot be rotated")
        if not credential.principal.is_active:
            raise CredentialStateConflict("target principal is inactive")
        current_scopes = normalize_scopes(credential.scopes, principal=credential.principal)
        successor = _insert_generated_credential(
            principal=credential.principal,
            name=credential.name,
            scopes=current_scopes,
            expires_at=_validate_expiry(now, expires_at),
            created_by=created_by,
            predecessor=credential,
            token_factory=token_factory,
            using=using,
        )
        credential.rotated_at = now
        credential.overlap_expires_at = now + selected_overlap
        credential.revision += 1
        credential.save(
            using=using,
            update_fields=("rotated_at", "overlap_expires_at", "revision", "updated_at"),
        )
        _audit_credential(
            actor_principal=actor,
            credential=successor.credential,
            action="management.credential.rotated",
            changes={
                "predecessor_id": str(credential.id),
                "overlap_seconds": int(selected_overlap.total_seconds()),
            },
            using=using,
        )
        return _response(successor)

    return execute_one_time_idempotent(
        principal=actor_principal,
        operation="management.credential.rotate",
        key=idempotency_key,
        request=request,
        command=command,
        using=using,
    )


def revoke_credential(
    *,
    actor_principal: APIPrincipal,
    credential_id: uuid.UUID,
    expected_revision: int,
    actor_permission: str,
    actor_credential: APICredential | None = None,
    actor_capability: Capability | None = None,
    using: str = "default",
) -> APICredential:
    with transaction.atomic(using=using):
        actor = lock_actor_authority(
            actor_principal,
            permission=actor_permission,
            using=using,
            actor_credential=actor_credential,
            actor_capability=actor_capability,
        )
        credential = APICredential.objects.using(using).get(pk=credential_id)
        if credential.revision != expected_revision:
            raise RevisionConflict(expected=expected_revision, actual=credential.revision)
        if credential.revoked_at is not None:
            return credential
        credential.revoked_at = timezone.now()
        credential.revision += 1
        credential.save(
            using=using,
            update_fields=("revoked_at", "revision", "updated_at"),
        )
        _audit_credential(
            actor_principal=actor,
            credential=credential,
            action="management.credential.revoked",
            changes={"revoked": True},
            using=using,
        )
        return credential


def revoke_credential_once(
    *,
    actor_principal: APIPrincipal,
    credential_id: uuid.UUID,
    expected_revision: int,
    idempotency_key: str,
    actor_permission: str,
    actor_credential: APICredential | None = None,
    actor_capability: Capability | None = None,
    using: str = "default",
) -> OneTimeCommandResult:
    request: JsonObject = {
        "credential_id": str(credential_id),
        "expected_revision": expected_revision,
    }

    def command() -> OneTimeCommandResult:
        credential = revoke_credential(
            actor_principal=actor_principal,
            credential_id=credential_id,
            expected_revision=expected_revision,
            actor_permission=actor_permission,
            actor_credential=actor_credential,
            actor_capability=actor_capability,
            using=using,
        )
        if credential.revoked_at is None:
            raise CredentialStateConflict("credential was not revoked")
        safe: JsonObject = {
            "credential_id": str(credential.id),
            "revoked_at": credential.revoked_at.isoformat(),
            "revision": credential.revision,
        }
        return OneTimeCommandResult(response=safe, safe_result=safe)

    return execute_one_time_idempotent(
        principal=actor_principal,
        operation="management.credential.revoke",
        key=idempotency_key,
        request=request,
        command=command,
        replay_safe=True,
        using=using,
    )


def note_credential_used(
    credential: APICredential,
    *,
    now=None,
    using: str = "default",
) -> bool:
    observed_at = now or timezone.now()
    cutoff = observed_at - LAST_USED_WRITE_INTERVAL
    updated = (
        APICredential.objects.using(using)
        .filter(
            pk=credential.pk,
            revoked_at__isnull=True,
        )
        .filter(Q(last_used_at__isnull=True) | Q(last_used_at__lte=cutoff))
        .update(last_used_at=observed_at)
    )
    return updated == 1
