"""Secret-safe, development-only owner bootstrap application service."""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from accounts.identity_values import normalize_account_email
from accounts.models import CustomUser
from accounts.studio_roles import (
    COURSE_REGISTRATION_COUNT_BASELINE_MANAGE,
    HISTORICAL_REGISTRATION_IMPORT_MANAGE,
    HISTORICAL_REGISTRATION_MAPPING_MANAGE,
    MANAGE_API_CREDENTIALS,
    SITE_NAVIGATION_READ,
    SITE_NAVIGATION_WRITE,
    SITE_SETTINGS_READ,
    SITE_SETTINGS_WRITE,
    SPONSORS_EXPORT,
    SPONSORS_READ,
    SPONSORS_WRITE,
    STUDIO_ACCESS,
    synchronize_studio_roles,
)
from accounts.studio_sessions import revoke_all_staff_sessions
from core.audit import AuditWriteContext, record_audit_event
from core.bootstrap import RuntimeEnvironment
from core.models import AuditEvent
from management_auth.models import APICredential, APIPrincipal

DEVELOPMENT_OWNER_USERNAME = "development-owner"
DEVELOPMENT_OWNER_PRINCIPAL = "human:development-owner"
DEVELOPMENT_AUTOMATION_NAME = "Development automation"
DEVELOPMENT_AUTOMATION_PRINCIPAL = "service:development-automation"


class DevelopmentOwnerBootstrapDenied(RuntimeError):
    """A safe, category-only bootstrap denial."""

    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(category)


@dataclass(frozen=True, slots=True)
class DevelopmentOwnerBootstrapResult:
    category: str
    users: int
    human_principals: int
    service_principals: int
    revoked_staff_sessions: int
    revoked_human_credentials: int


def _permission(name: str, *, using: str) -> Permission:
    app_label, codename = name.split(".", 1)
    permission = (
        Permission.objects.using(using)
        .filter(content_type__app_label=app_label, codename=codename)
        .first()
    )
    if permission is None:
        raise DevelopmentOwnerBootstrapDenied("authority_unavailable")
    return permission


def _audit(
    *,
    action: str,
    outcome: str,
    category: str,
    using: str,
    actor_id: int | None = None,
) -> None:
    record_audit_event(
        action=action,
        target_type="accounts.development_owner",
        target_label="development-owner",
        outcome=outcome,
        context=AuditWriteContext(
            actor_id=actor_id,
            actor_ref=f"user:{actor_id}" if actor_id is not None else "operator:bootstrap",
        ),
        changes={},
        metadata={"category": category},
        using=using,
    )


def _deny(category: str, *, using: str) -> None:
    _audit(
        action="accounts.development_owner.bootstrap",
        outcome=AuditEvent.Outcome.DENIED,
        category=category,
        using=using,
    )
    raise DevelopmentOwnerBootstrapDenied(category)


def _runtime_allowed(*, allow_test: bool) -> bool:
    runtime = getattr(settings, "RUNTIME_ENVIRONMENT", None)
    return runtime is RuntimeEnvironment.DEVELOPMENT or (
        allow_test and runtime is RuntimeEnvironment.TEST
    )


def _matching_users(normalized_email: str, *, using: str) -> tuple[CustomUser, ...]:
    matches: list[CustomUser] = []
    for user in get_user_model().objects.using(using).order_by("pk").iterator():
        candidate = user.normalized_email or normalize_account_email(user.email)
        if candidate == normalized_email:
            matches.append(user)
    return tuple(matches)


def development_owner_exists(*, using: str = "default") -> bool:
    return (
        APIPrincipal.objects.using(using)
        .filter(
            kind=APIPrincipal.Kind.HUMAN,
            identity_snapshot=DEVELOPMENT_OWNER_PRINCIPAL,
        )
        .exists()
    )


def bootstrap_development_owner(
    *,
    email: str,
    password: str | None,
    reset_password: bool,
    allow_test: bool = False,
    using: str = "default",
) -> DevelopmentOwnerBootstrapResult:
    """Create or reconcile one exact development owner without persisting input."""

    if not _runtime_allowed(allow_test=allow_test):
        raise DevelopmentOwnerBootstrapDenied("environment_denied")
    normalized_email = normalize_account_email(email)
    if normalized_email is None:
        _deny("identity_invalid", using=using)
    if not isinstance(reset_password, bool):
        _deny("confirmation_invalid", using=using)
    if password is not None and (not isinstance(password, str) or len(password) < 12):
        _deny("password_invalid", using=using)

    matching_users = _matching_users(normalized_email, using=using)
    if len(matching_users) > 1:
        _deny("identity_conflict", using=using)
    owner_principals = tuple(
        APIPrincipal.objects.using(using)
        .select_related("user")
        .filter(
            kind=APIPrincipal.Kind.HUMAN,
            identity_snapshot=DEVELOPMENT_OWNER_PRINCIPAL,
        )
    )
    if len(owner_principals) > 1:
        _deny("owner_conflict", using=using)
    service_principals = APIPrincipal.objects.using(using).filter(
        identity_snapshot=DEVELOPMENT_AUTOMATION_PRINCIPAL
    )
    if service_principals.count() > 1:
        _deny("service_conflict", using=using)
    existing_owner = owner_principals[0] if owner_principals else None
    if existing_owner is not None:
        if not matching_users or existing_owner.user_id != matching_users[0].pk:
            _deny("second_owner_denied", using=using)
    site_admin = Group.objects.using(using).filter(name="site_admin").first()
    if site_admin is not None:
        other_site_admin = site_admin.user_set.using(using).exclude(
            pk=matching_users[0].pk if matching_users else None
        )
        if other_site_admin.exists():
            _deny("second_owner_denied", using=using)

    existing_user = matching_users[0] if matching_users else None
    creating = existing_user is None
    if creating and password is None:
        _deny("password_required", using=using)
    if existing_user is not None and existing_owner is None and not reset_password:
        _deny("reset_confirmation_required", using=using)
    if reset_password and password is None:
        _deny("password_required", using=using)

    with transaction.atomic(using=using):
        synchronize_studio_roles()
        site_admin = Group.objects.using(using).get(name="site_admin")
        studio_access = _permission(STUDIO_ACCESS, using=using)
        credential_management = _permission(MANAGE_API_CREDENTIALS, using=using)
        historical_import = _permission(HISTORICAL_REGISTRATION_IMPORT_MANAGE, using=using)
        historical_mapping = _permission(HISTORICAL_REGISTRATION_MAPPING_MANAGE, using=using)
        course_count_baseline = _permission(
            COURSE_REGISTRATION_COUNT_BASELINE_MANAGE, using=using
        )
        site_settings_read = _permission(SITE_SETTINGS_READ, using=using)
        site_settings_write = _permission(SITE_SETTINGS_WRITE, using=using)
        site_navigation_read = _permission(SITE_NAVIGATION_READ, using=using)
        site_navigation_write = _permission(SITE_NAVIGATION_WRITE, using=using)
        sponsors_read = _permission(SPONSORS_READ, using=using)
        sponsors_write = _permission(SPONSORS_WRITE, using=using)
        sponsors_export = _permission(SPONSORS_EXPORT, using=using)

        if existing_user is None:
            if (
                get_user_model()
                .objects.using(using)
                .filter(username=DEVELOPMENT_OWNER_USERNAME)
                .exists()
            ):
                raise DevelopmentOwnerBootstrapDenied("identity_conflict")
            user = (
                get_user_model()
                .objects.db_manager(using)
                .create_user(
                    username=DEVELOPMENT_OWNER_USERNAME,
                    email=normalized_email,
                    password=password,
                    identity_state=CustomUser.IdentityState.ACTIVE,
                    is_active=True,
                    is_staff=True,
                    is_superuser=True,
                )
            )
        else:
            user = existing_user
            user.email = normalized_email
            user.identity_state = CustomUser.IdentityState.ACTIVE
            user.is_active = True
            user.is_staff = True
            user.is_superuser = True
            if reset_password:
                user.set_password(password)
            user_update_fields = [
                "email",
                "normalized_email",
                "identity_state",
                "is_active",
                "is_staff",
                "is_superuser",
            ]
            if reset_password:
                user_update_fields.append("password")
            user.save(
                using=using,
                update_fields=user_update_fields,
            )
        user.groups.add(site_admin)

        human, human_created = APIPrincipal.objects.using(using).get_or_create(
            user=user,
            defaults={
                "kind": APIPrincipal.Kind.HUMAN,
                "name": "Development owner",
                "identity_snapshot": DEVELOPMENT_OWNER_PRINCIPAL,
                "created_by": user,
            },
        )
        if not human_created and (
            human.kind != APIPrincipal.Kind.HUMAN
            or human.identity_snapshot != DEVELOPMENT_OWNER_PRINCIPAL
        ):
            raise DevelopmentOwnerBootstrapDenied("identity_conflict")
        if not human.is_active:
            human.is_active = True
            human.revision += 1
            human.save(
                using=using,
                update_fields=("is_active", "revision", "updated_at"),
            )
        human.permissions.set(
            (
                studio_access,
                credential_management,
                site_settings_read,
                site_settings_write,
                site_navigation_read,
                site_navigation_write,
                sponsors_read,
                sponsors_write,
                sponsors_export,
            )
        )

        service, service_created = APIPrincipal.objects.using(using).get_or_create(
            identity_snapshot=DEVELOPMENT_AUTOMATION_PRINCIPAL,
            defaults={
                "kind": APIPrincipal.Kind.SERVICE,
                "name": DEVELOPMENT_AUTOMATION_NAME,
                "created_by": user,
            },
        )
        if service.kind != APIPrincipal.Kind.SERVICE or service.user_id is not None:
            raise DevelopmentOwnerBootstrapDenied("service_conflict")
        if not service.is_active:
            service.is_active = True
            service.revision += 1
            service.save(
                using=using,
                update_fields=("is_active", "revision", "updated_at"),
            )
        service.permissions.set(
            (
                studio_access,
                historical_import,
                historical_mapping,
                course_count_baseline,
                site_settings_read,
                site_settings_write,
                site_navigation_read,
                site_navigation_write,
                sponsors_read,
                sponsors_write,
            )
        )

        revoked_sessions = 0
        revoked_credentials = 0
        action = "accounts.development_owner.bootstrap"
        category = "created" if creating else "reconciled"
        if reset_password and not creating:
            now = timezone.now()
            revoked_sessions = revoke_all_staff_sessions(user, at=now)
            revoked_credentials = (
                APICredential.objects.using(using)
                .filter(
                    principal=human,
                    revoked_at__isnull=True,
                )
                .update(revoked_at=now, revision=F("revision") + 1, updated_at=now)
            )
            action = "accounts.development_owner.reset"
            category = "reset"
        _audit(
            action=action,
            outcome=AuditEvent.Outcome.SUCCEEDED,
            category=category,
            using=using,
            actor_id=user.pk,
        )

    return DevelopmentOwnerBootstrapResult(
        category=category,
        users=1,
        human_principals=1,
        service_principals=1,
        revoked_staff_sessions=revoked_sessions,
        revoked_human_credentials=revoked_credentials,
    )
