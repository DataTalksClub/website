from __future__ import annotations

from dataclasses import asdict
from typing import Any, cast

from allauth.account.adapter import get_adapter as get_account_adapter
from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Model
from django.db.models.fields.reverse_related import ForeignObjectRel, ManyToManyRel
from django.urls import NoReverseMatch, reverse

from accounts.identity_relations import (
    ACCOUNT_EXTENSION_MODELS,
    ACCOUNT_RELATIONS,
)
from accounts.identity_relations import (
    AccountRelationSpec as AccountRelationSpec,
)
from accounts.identity_relationship_evidence import (
    append_only_relation_keys as append_only_relation_keys,
)
from accounts.identity_relationship_evidence import (
    extension_relation_keys as extension_relation_keys,
)
from accounts.identity_relationship_evidence import (
    relationship_evidence as relationship_evidence,
)
from accounts.identity_relationship_evidence import (
    relationship_row_identities as relationship_row_identities,
)
from accounts.identity_values import canonical_json, sha256_text

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
        "owner_model": "accounts.User",
        "field_name": "groups",
        "through_table": "accounts_user_groups",
        "user_field": "user",
        "handling": "source_authority_only",
    },
    {
        "owner_model": "accounts.User",
        "field_name": "user_permissions",
        "through_table": "accounts_user_user_permissions",
        "user_field": "user",
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


def _model_graph_relation_keys(user_model) -> set[str]:
    """Every relation the model graph declares onto ``user_model``.

    Walks ``related_objects`` (hidden auto-created many-to-many through
    models included, since those are how ``groups``/``user_permissions``
    reach ``ACCOUNT_RELATIONS`` at all) instead of the hand-written list, so
    the guard sees what Django itself sees. A many-to-many relation that
    goes through an explicit model (``courses.Cohort.students`` through
    ``courses.Enrollment``) is left out: the through model owns its own
    foreign key to the user and that key is already its own separate entry
    here, so counting the many-to-many relation too would be the same
    relation twice under two different keys.
    """
    keys = set()
    for field in user_model._meta.get_fields(include_hidden=True):
        if not isinstance(field, ForeignObjectRel):
            continue
        if isinstance(field, ManyToManyRel) and field.through is not None:
            continue
        keys.add(f"{cast(type[Model], field.related_model)._meta.label}.{field.field.name}")
    return keys


def unclassified_account_relations() -> list[str]:
    """Relations onto the account that this module does not yet classify.

    A relation is classified either by naming it in ``ACCOUNT_RELATIONS`` or
    by its model being one of ``ACCOUNT_EXTENSION_MODELS`` (the account's own
    extension rows, already enumerated by field rather than by relation).
    Anything else the model graph declares onto the user model shows up
    here, so a relation nobody has decided about yet fails the guard instead
    of going quietly uncounted -- see D3.3 and playbook P7.
    """
    User = get_user_model()
    named = {spec.key for spec in ACCOUNT_RELATIONS}
    extension_labels = {model_label for model_label, _ in ACCOUNT_EXTENSION_MODELS}
    unclassified = []
    for key in _model_graph_relation_keys(User):
        if key not in named and key.rsplit(".", 1)[0] not in extension_labels:
            unclassified.append(key)
    return sorted(unclassified)


def stale_account_relations() -> list[str]:
    """``ACCOUNT_RELATIONS`` entries with no matching relation in the model graph.

    The other half of the same guard (P7: "enumerate from both directions"):
    a spec can go stale the same way an unnamed relation can go uncounted,
    if the field it names is renamed or removed without updating this list.
    """
    User = get_user_model()
    graph_keys = _model_graph_relation_keys(User)
    stale = []
    for spec in ACCOUNT_RELATIONS:
        if spec.key not in graph_keys:
            stale.append(spec.key)
    return sorted(stale)


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


def _account_fields(model, *, skip: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    collected = []
    for field in model._meta.get_fields():
        if field.auto_created and not field.concrete:
            continue
        if field.name in skip:
            continue
        collected.append(
            {
                "name": field.name,
                "model_label": model._meta.label,
                "table": model._meta.db_table,
                "column": getattr(field, "column", None),
                "type": field.get_internal_type(),
                "null": getattr(field, "null", False),
                "unique": getattr(field, "unique", False),
                "classification": _field_classification(field.name),
            }
        )
    return collected


def _inventory_storage():
    User = get_user_model()
    fields = _account_fields(User)
    for model_label, skip in ACCOUNT_EXTENSION_MODELS:
        fields.extend(_account_fields(apps.get_model(model_label), skip=skip))
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
    return fields, relations


def _inventory_session():
    return {
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


def _inventory_routes():
    providers = []
    for app in settings.INSTALLED_APPS:
        if app.startswith("allauth.socialaccount.providers."):
            providers.append(app.rsplit(".", 1)[-1])
    navigation = _routes(ACCOUNT_NAVIGATION_ACTIONS)
    authentication_routes = _routes(ACCOUNT_AUTHENTICATION_ROUTES)
    return sorted(providers), navigation, authentication_routes


def _routes(actions):
    return [
        {"action": action, "route_name": name, "path": _route(name)} for action, name in actions
    ]


def _inventory_policies():
    return {
        "compatibility_identifiers": [
            "accounts.User.id",
            "accounts.User.username",
            "accounts.User.email",
            "accounts.Token.key (never emitted)",
            "accounts_ext.AccountIdentityAlias.source_user_id",
        ],
        "public_person_policy": "editorial_identity_never_authentication",
        "content_projection_account_creation": False,
    }


def _inventory_report(User, fields, relations):
    providers, navigation, authentication_routes = _inventory_routes()
    session = _inventory_session()
    return {
        "schema_version": "single-durable-account-inventory-v1",
        "auth_user_model": settings.AUTH_USER_MODEL,
        "user_table": User._meta.db_table,
        "authentication_backends": list(settings.AUTHENTICATION_BACKENDS),
        "account_login_methods": sorted(settings.ACCOUNT_LOGIN_METHODS),
        "account_registration_enabled": get_account_adapter().is_open_for_signup(None),
        "account_fields": fields,
        "dependent_relations": relations,
        "many_to_many_relations": list(ACCOUNT_MANY_TO_MANY_RELATIONS),
        "session": session,
        "providers": providers,
        "provider_claim_policy": "verified_adapter_evidence_only",
        "authentication_routes": authentication_routes,
        "navigation": navigation,
        **_inventory_policies(),
    }


def account_inventory() -> dict[str, Any]:
    fields, relations = _inventory_storage()
    report = _inventory_report(get_user_model(), fields, relations)
    report["inventory_checksum"] = sha256_text(canonical_json(report))
    return report
