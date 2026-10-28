"""Device-login credential minting for the shared device flow.

The package calls ``mint_device_credential(*, user, label, request)`` after
an admin approves a grant in the browser. It issues a management
``APICredential`` on the approver's human principal with the entitled
course-launch scopes, so the CLI works exactly like a Studio-issued scoped
token. Least privilege is enforced throughout: only scopes the approver
explicitly holds are minted, and an approver without explicit launch
permissions fails closed instead of minting.

``is_device_login_admin`` is the site admin definition for this surface
(``DEVICE_LOGIN_ADMIN_CHECK``): an active, eligible user with explicit
course-launch management permission. The site_admin role carries it;
``is_staff``/superuser alone never bypass the registry.
"""

from __future__ import annotations

from community_base.kernel.redaction import is_sensitive_text
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.identity_resolution import identity_state_eligible
from accounts.studio_authorization import has_explicit_permission
from management_auth.constants import (
    DEFAULT_CREDENTIAL_LIFETIME,
    DIGEST_ALGORITHM,
    DIGEST_VERSION,
    PREFIX_COLLISION_RETRIES,
)
from management_auth.models import APIPrincipal
from management_auth.services import (
    _audit_credential,
    create_principal,
    normalize_scopes,
)
from management_auth.tokens import encode_secret, generate_token

LAUNCH_SCOPES = (
    "courses.launch.read",
    "courses.launch.write",
    "courses.launch.send",
)
_LAUNCH_MANAGE_PERMISSION = "courses.manage_course_launch"


def is_device_login_admin(user) -> bool:
    return bool(
        getattr(user, "is_active", False)
        and identity_state_eligible(user)
        and has_explicit_permission(user, _LAUNCH_MANAGE_PERMISSION)
    )


def _principal_for(user) -> APIPrincipal:
    principal = (
        APIPrincipal.objects.filter(kind=APIPrincipal.Kind.HUMAN, user=user)
        .order_by("-created_at")
        .first()
    )
    if principal is not None:
        if not principal.is_active:
            raise ValueError("management principal is inactive")
        return principal
    # Names and snapshots never carry PII (core redaction policy): the user
    # row already links the principal to its owner.
    return create_principal(
        kind=APIPrincipal.Kind.HUMAN,
        name=f"Device login {user.pk}",
        identity_snapshot=f"device-login:{user.pk}",
        user=user,
        created_by=user,
    )


def _credential_name(label: str) -> str:
    candidate = f"Device login {label}".strip()[:120]
    if not candidate or is_sensitive_text(candidate):
        return "Device login"
    return candidate


def mint_device_credential(*, user, label: str, request) -> dict:
    from django.contrib.auth.models import Permission

    from management_auth.models import APICredential
    from management_registry import CAPABILITY_REGISTRY

    principal = _principal_for(user)
    # The fresh principal carries no permissions of its own; mirror the
    # approver's explicit grants onto it (never more) so scope checks see
    # the same authority the user already holds. Human checks always
    # re-verify the live user grant, so later revocation still fails closed.
    entitled = []
    for scope in LAUNCH_SCOPES:
        capability = CAPABILITY_REGISTRY.get(scope)
        if capability is None:
            raise ValueError(f"unknown credential scope: {scope}")
        app_label, codename = capability.django_permission.split(".", 1)
        if not has_explicit_permission(user, capability.django_permission):
            continue
        permission = Permission.objects.filter(
            content_type__app_label=app_label, codename=codename
        ).first()
        if permission is None:
            continue
        principal.permissions.add(permission)
        entitled.append(scope)
    if "courses.launch.read" not in entitled:
        raise ValueError("approver needs course launch read access")
    scopes = normalize_scopes(entitled, principal=principal)
    name = _credential_name(label)
    expires_at = timezone.now() + DEFAULT_CREDENTIAL_LIFETIME
    for _ in range(PREFIX_COLLISION_RETRIES):
        generated = generate_token()
        try:
            with transaction.atomic():
                credential = APICredential.objects.create(
                    principal=principal,
                    name=name,
                    prefix=generated.prefix,
                    secret_digest=encode_secret(generated.secret),
                    digest_algorithm=DIGEST_ALGORITHM,
                    digest_version=DIGEST_VERSION,
                    scopes=list(scopes),
                    expires_at=expires_at,
                    created_by=user,
                )
                _audit_credential(
                    actor_principal=principal,
                    credential=credential,
                    action="management.credential.created",
                    operation="management.credential.create",
                    idempotency_key="",
                    changes={"created": True, "source": "device_login"},
                    using="default",
                    extra_metadata={"source": "device_login", "device_label": name},
                )
        except IntegrityError:
            continue
        return {
            "token": generated.raw,
            "expires_at": expires_at.isoformat(),
            "user": {
                "email": getattr(user, "email", ""),
                "name": getattr(user, "get_full_name", lambda: "")() or "",
            },
        }
    raise ValueError("credential prefix allocation failed")
