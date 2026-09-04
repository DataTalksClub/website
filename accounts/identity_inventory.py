from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from allauth.account.adapter import get_adapter as get_account_adapter
from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import NoReverseMatch, reverse

from accounts.identity_values import canonical_json, sha256_text


@dataclass(frozen=True, slots=True)
class AccountRelationSpec:
    model_label: str
    field_name: str
    handling: str

    @property
    def key(self) -> str:
        return f"{self.model_label}.{self.field_name}"


ACCOUNT_RELATIONS = (
    AccountRelationSpec(
        "accounts.CustomUser_groups",
        "customuser",
        "source_authority_only",
    ),
    AccountRelationSpec(
        "accounts.CustomUser_user_permissions",
        "customuser",
        "source_authority_only",
    ),
    AccountRelationSpec("admin.LogEntry", "user", "provenance_alias"),
    AccountRelationSpec(
        "management_auth.APIPrincipal",
        "user",
        "disable_source_principal",
    ),
    AccountRelationSpec(
        "management_auth.APIPrincipal",
        "created_by",
        "provenance_alias",
    ),
    AccountRelationSpec(
        "management_auth.APICredential",
        "created_by",
        "provenance_alias",
    ),
    AccountRelationSpec("core.AuditEvent", "actor", "provenance_alias"),
    AccountRelationSpec("core.StaffSession", "user", "reparent"),
    AccountRelationSpec("core.Operation", "actor", "provenance_alias"),
    AccountRelationSpec("accounts.Token", "user", "compatibility_alias"),
    AccountRelationSpec("courses.CourseRegistration", "user", "reparent"),
    AccountRelationSpec("courses.Enrollment", "student", "reparent"),
    AccountRelationSpec("courses.Submission", "student", "reparent"),
    AccountRelationSpec("courses.ProjectSubmission", "student", "reparent"),
    AccountRelationSpec("courses.LeaderboardComplaint", "reporter", "reparent"),
    AccountRelationSpec("courses.LeaderboardComplaint", "resolved_by", "reparent"),
    AccountRelationSpec("courses.ProjectVote", "voter", "reparent"),
    AccountRelationSpec("courses.UserWrappedStatistics", "user", "reparent"),
    AccountRelationSpec("account.EmailAddress", "user", "verified_only"),
    AccountRelationSpec("socialaccount.SocialAccount", "user", "verified_only"),
)

# What the account menu offers.  Sign-in methods are no longer one of its
# entries: they are a section of account settings, and socialaccount_connections
# redirects there.  The route itself stays in ACCOUNT_AUTHENTICATION_ROUTES
# below, because it still exists and allauth still reverses it.
ACCOUNT_NAVIGATION_ACTIONS = (
    ("signed_out_login", "login"),
    ("account_settings", "account_settings"),
    ("course_discovery", "course_list"),
    ("studio", "studio:home"),
    ("logout", "account_logout"),
)

ACCOUNT_AUTHENTICATION_ROUTES = (
    ("login", "login"),
    ("logout", "account_logout"),
    ("settings", "account_settings"),
    ("social_connections", "socialaccount_connections"),
    ("github_login", "github_login"),
    ("github_callback", "github_callback"),
    ("google_login", "google_login"),
    ("google_callback", "google_callback"),
    ("slack_login", "slack_login"),
    ("slack_callback", "slack_callback"),
)

ACCOUNT_MANY_TO_MANY_RELATIONS = (
    {
        "owner_model": "accounts.CustomUser",
        "field_name": "groups",
        "through_table": "accounts_customuser_groups",
        "user_field": "customuser",
        "handling": "source_authority_only",
    },
    {
        "owner_model": "accounts.CustomUser",
        "field_name": "user_permissions",
        "through_table": "accounts_customuser_user_permissions",
        "user_field": "customuser",
        "handling": "source_authority_only",
    },
    {
        "owner_model": "courses.Cohort",
        "field_name": "students",
        "through_table": "courses_enrollment",
        "user_field": "student",
        "handling": "reparent_via_enrollment",
    },
)


def _field_classification(name: str) -> str:
    if name in {
        "id",
        "username",
        "email",
        "normalized_email",
        "identity_state",
        "password",
        "last_login",
        "date_joined",
    }:
        return "identity"
    if name in {
        "is_active",
        "is_staff",
        "is_superuser",
        "groups",
        "user_permissions",
        "role",
    }:
        return "authority"
    if name in {"dark_mode", "preferred_timezone"}:
        return "preference"
    return "profile"


def _route(name: str) -> str:
    try:
        return reverse(name)
    except NoReverseMatch:
        return "unavailable"


def account_inventory() -> dict[str, Any]:
    User = get_user_model()
    fields = []
    for field in User._meta.get_fields():
        if field.auto_created and not field.concrete:
            continue
        fields.append(
            {
                "name": field.name,
                "column": getattr(field, "column", None),
                "type": field.get_internal_type(),
                "null": getattr(field, "null", False),
                "unique": getattr(field, "unique", False),
                "classification": _field_classification(field.name),
            }
        )
    relations = []
    for spec in ACCOUNT_RELATIONS:
        model = apps.get_model(spec.model_label)
        field = model._meta.get_field(spec.field_name)
        relations.append(
            {
                **asdict(spec),
                "table": model._meta.db_table,
                "column": field.column,
                "nullable": field.null,
                "on_delete": getattr(field.remote_field.on_delete, "__name__", ""),
            }
        )
    session = {
        "engine": settings.SESSION_ENGINE,
        "cookie_name": settings.SESSION_COOKIE_NAME,
        "cookie_domain": settings.SESSION_COOKIE_DOMAIN,
        "cookie_secure": settings.SESSION_COOKIE_SECURE,
        "cookie_httponly": settings.SESSION_COOKIE_HTTPONLY,
        "cookie_samesite": settings.SESSION_COOKIE_SAMESITE,
        "cookie_age_seconds": settings.SESSION_COOKIE_AGE,
        "save_every_request": settings.SESSION_SAVE_EVERY_REQUEST,
        "expire_at_browser_close": settings.SESSION_EXPIRE_AT_BROWSER_CLOSE,
        "cross_host_policy": "explicit_reauthentication",
    }
    providers = sorted(
        app.rsplit(".", 1)[-1]
        for app in settings.INSTALLED_APPS
        if app.startswith("allauth.socialaccount.providers.")
    )
    navigation = [
        {"action": action, "route_name": name, "path": _route(name)}
        for action, name in ACCOUNT_NAVIGATION_ACTIONS
    ]
    authentication_routes = [
        {"action": action, "route_name": name, "path": _route(name)}
        for action, name in ACCOUNT_AUTHENTICATION_ROUTES
    ]
    report = {
        "schema_version": "single-durable-account-inventory-v1",
        "auth_user_model": settings.AUTH_USER_MODEL,
        "user_table": User._meta.db_table,
        "authentication_backends": list(settings.AUTHENTICATION_BACKENDS),
        "account_login_methods": sorted(settings.ACCOUNT_LOGIN_METHODS),
        # Read the adapter's actual gate rather than a setting nobody
        # enforces, so this report cannot drift from what `/accounts/signup/`
        # really does.  No request is in play here; `ClosedAccountAdapter`
        # (and allauth's own `DefaultAccountAdapter`) ignore it.
        "account_registration_enabled": get_account_adapter().is_open_for_signup(None),
        "account_fields": fields,
        "dependent_relations": relations,
        "many_to_many_relations": list(ACCOUNT_MANY_TO_MANY_RELATIONS),
        "session": session,
        "providers": providers,
        "provider_claim_policy": "verified_adapter_evidence_only",
        "authentication_routes": authentication_routes,
        "navigation": navigation,
        "compatibility_identifiers": [
            "accounts.CustomUser.id",
            "accounts.CustomUser.username",
            "accounts.CustomUser.email",
            "accounts.Token.key (never emitted)",
            "accounts.AccountIdentityAlias.source_user_id",
        ],
        "public_person_policy": "editorial_identity_never_authentication",
        "content_projection_account_creation": False,
    }
    report["inventory_checksum"] = sha256_text(canonical_json(report))
    return report


def relationship_evidence(
    *,
    alias_overrides: dict[int, int] | None = None,
) -> tuple[dict[str, int], dict[str, str]]:
    from accounts.models import AccountIdentityAlias

    aliases = dict(
        AccountIdentityAlias.objects.values_list(
            "source_user_id",
            "survivor_id",
        )
    )
    aliases.update(alias_overrides or {})
    counts: dict[str, int] = {}
    checksums: dict[str, str] = {}
    for spec in ACCOUNT_RELATIONS:
        model = apps.get_model(spec.model_label)
        rows = list(
            model._base_manager.exclude(**{f"{spec.field_name}__isnull": True})
            .order_by("pk")
            .values_list("pk", f"{spec.field_name}_id")
        )
        logical_rows = [
            [str(row_id), aliases.get(int(user_id), int(user_id))] for row_id, user_id in rows
        ]
        counts[spec.key] = len(rows)
        checksums[spec.key] = sha256_text(canonical_json({"rows": logical_rows}))
    return counts, checksums
