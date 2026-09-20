from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from allauth.account.adapter import get_adapter as get_account_adapter
from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models.fields.reverse_related import ForeignObjectRel, ManyToManyRel
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
        "accounts.User_groups",
        "user",
        "source_authority_only",
    ),
    AccountRelationSpec(
        "accounts.User_user_permissions",
        "user",
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
    # D3.3: the rest of this tuple was found by deriving the guard from
    # ``User._meta.related_objects`` instead of trusting this list to be
    # exhaustive (see ``unclassified_account_relations`` below). Every one of
    # these predates D3.3; none of them is the two relations that issue was
    # filed about (those live in ACCOUNT_EXTENSION_MODELS, not here). Each is
    # classified on its own rather than exempted as a group.
    #
    # The shared ``community_base.events`` app has owned the "events" label
    # and taken live writes since D4.1 (see website/settings/base.py), so a
    # merge must move a person's registrations the same way it already moves
    # ``courses.Enrollment``/``courses.CourseRegistration`` rows.
    AccountRelationSpec("events.EventRegistration", "user", "reparent"),
    AccountRelationSpec("events.SeriesRegistration", "user", "reparent"),
    AccountRelationSpec("events.SeriesOccurrenceOptOut", "user", "reparent"),
    # community_base.mail's EmailDelivery has taken real writes since D1.2b
    # (course_management/package_mail.py sends five production purposes
    # through it). idempotency_key is globally unique, not scoped to the
    # recipient, so reparenting never collides with it.
    AccountRelationSpec("cb_mail.EmailDelivery", "recipient_user", "reparent"),
    # Package Studio notes are durable member-owned account data. The note
    # follows the surviving account; its author is audit provenance, matching
    # the site's other nullable actor relations.
    AccountRelationSpec("cb_studio.MemberNote", "member", "reparent"),
    AccountRelationSpec("cb_studio.MemberNote", "created_by", "provenance_alias"),
    # community_base.coursework/curriculum are installed but explicitly not
    # yet served (website/settings/base.py: "Nothing serves from these
    # tables until the D5.2 route flip"; scripts/prod/import_shared_course_platform.py
    # is their only writer, and it has not run). Each one mirrors an
    # already-reparented ``courses.*`` model field-for-field, so classifying
    # them "reparent" now is a no-op today and correct without a follow-up
    # once D5.1/D5.2 land -- the alternative, leaving them for that phase to
    # notice, is exactly the kind of drift this issue exists to close.
    AccountRelationSpec("cb_coursework.CourseRegistration", "user", "reparent"),
    AccountRelationSpec("cb_coursework.Submission", "student", "reparent"),
    AccountRelationSpec("cb_coursework.ProjectSubmission", "student", "reparent"),
    AccountRelationSpec("cb_coursework.LeaderboardComplaint", "reporter", "reparent"),
    AccountRelationSpec("cb_coursework.LeaderboardComplaint", "resolved_by", "reparent"),
    AccountRelationSpec("cb_coursework.ProjectVote", "voter", "reparent"),
    AccountRelationSpec("cb_coursework.UserWrappedStatistics", "user", "reparent"),
    AccountRelationSpec("cb_curriculum.Enrollment", "user", "reparent"),
    AccountRelationSpec("cb_curriculum.UnitProgress", "user", "reparent"),
    # courses.UnitReadState/SharedLessonReadState are live per-account read
    # markers (courses/services/unit_read_state.py, member_home.py). Each
    # carries a unique constraint on (user, unit)/(user, shared_lesson), the
    # same shape ``courses.Enrollment``'s already-accepted ``unique_together
    # = ["student", "course"]`` has under "reparent" today: a collision
    # aborts the whole merge with an IntegrityError inside the same
    # transaction rather than silently dropping a row, which is the existing
    # risk profile for every reparented relation, not a new one.
    AccountRelationSpec("courses.UnitReadState", "user", "reparent"),
    AccountRelationSpec("courses.SharedLessonReadState", "user", "reparent"),
    # cb_api.APIKey.user: community_base.api is installed (website/settings
    # /base.py) but no urlconf, view, management command, or job in this
    # site ever includes its urls or imports the model -- website/urls.py
    # has no route for it. Nothing can create a row, so there is no row a
    # merge could lose. Revisit if this site ever wires the app in.
    AccountRelationSpec("cb_api.APIKey", "user", "no_handling_required"),
    # event_registrants.EventRegistrantIdentity.account is a nullable
    # OneToOne written only by scripts/prod/registrant_import.py and its
    # siblings, which resolve it by ``normalized_email`` against
    # ``accounts_user`` on every run (see the model's own docstring: "a
    # future import that matches this address onto a real account attaches
    # through the same account-first lookup on its next run"). A merge
    # reparenting it here would fight that reconciliation and, since the
    # field is a plain (non-unique-safe) OneToOne, could collide if the
    # survivor already has its own row; letting the next import re-resolve
    # both accounts to the one survivor is the correct convergence, not a
    # gap.
    AccountRelationSpec(
        "event_registrants.EventRegistrantIdentity",
        "account",
        "no_handling_required",
    ),
    # accounts_ext.AccountIdentityAlias.survivor is the merge's own ledger --
    # the row this relation lives on *is* the record of a previous merge, not
    # a fact about the survivor that a later merge could leave stale.
    # Reparenting it onto a new survivor when the current survivor is itself
    # later absorbed would rewrite the history it exists to preserve, in
    # place of the row a chained merge should add. Unlike the two
    # ``no_handling_required`` entries above, this table is *not* static
    # during an apply: every successful merge creates exactly one new row
    # here (the alias just recorded), so it needs the same append-only
    # evidence rule as the extension tables, not the plain-equality rule
    # ``no_handling_required`` gets -- see ``append_only_relation_keys``.
    AccountRelationSpec(
        "accounts_ext.AccountIdentityAlias",
        "survivor",
        "merge_ledger_append_only",
    ),
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


# The durable account is the user row plus its extension rows. The D3.1 field
# move took twelve fields off the user model; it did not take them out of the
# account, and the reviewed merge still decides ten of them
# (``scripts.prod.account_reconciliation.PROFILE_FIELDS``) while the identity
# window still turns on the other two. So the inventory enumerates all three
# models: a report that walked only the user model would quietly stop naming
# them, which is precisely what an operator reads this report to find out.
# The join column and each extension row's surrogate key are not account
# fields and are left out.
ACCOUNT_EXTENSION_MODELS: tuple[tuple[str, frozenset[str]], ...] = (
    ("accounts_ext.IdentityState", frozenset({"id", "user"})),
    ("courses.LearnerProfile", frozenset({"id", "user"})),
)


def _extension_join_field(model_label: str, skip: frozenset[str]) -> str:
    # The join column's name is not stored anywhere else; find it rather
    # than assume it is always "user" so a differently-named future
    # extension model does not silently miscount.
    User = get_user_model()
    model = apps.get_model(model_label)
    for name in skip:
        field = model._meta.get_field(name)
        if getattr(field, "related_model", None) is User:
            return name
    raise LookupError(f"{model_label} has no relation to the user model within {sorted(skip)!r}")


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
        keys.add(f"{field.related_model._meta.label}.{field.field.name}")
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
    return sorted(
        key
        for key in _model_graph_relation_keys(User)
        if key not in named and key.rsplit(".", 1)[0] not in extension_labels
    )


def stale_account_relations() -> list[str]:
    """``ACCOUNT_RELATIONS`` entries with no matching relation in the model graph.

    The other half of the same guard (P7: "enumerate from both directions"):
    a spec can go stale the same way an unnamed relation can go uncounted,
    if the field it names is renamed or removed without updating this list.
    """
    User = get_user_model()
    graph_keys = _model_graph_relation_keys(User)
    return sorted(spec.key for spec in ACCOUNT_RELATIONS if spec.key not in graph_keys)


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


def account_inventory() -> dict[str, Any]:
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
        # really does.  No request is in play here; `AccountAdapter`
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
            "accounts.User.id",
            "accounts.User.username",
            "accounts.User.email",
            "accounts.Token.key (never emitted)",
            "accounts_ext.AccountIdentityAlias.source_user_id",
        ],
        "public_person_policy": "editorial_identity_never_authentication",
        "content_projection_account_creation": False,
    }
    report["inventory_checksum"] = sha256_text(canonical_json(report))
    return report


def _relation_logical_rows(
    *,
    model_label: str,
    field_name: str,
    aliases: dict[int, int],
) -> list[list[str | int]]:
    model = apps.get_model(model_label)
    rows = list(
        model._base_manager.exclude(**{f"{field_name}__isnull": True})
        .order_by("pk")
        .values_list("pk", f"{field_name}_id")
    )
    return [[str(row_id), aliases.get(int(user_id), int(user_id))] for row_id, user_id in rows]


def _relations_and_keys() -> list[tuple[str, str, str]]:
    # (key, model_label, field_name) for every relation this module tracks:
    # the named ACCOUNT_RELATIONS plus, D3.3, the account's own extension
    # rows -- the two tables the reviewed merge writes that ACCOUNT_RELATIONS
    # itself does not name (see ACCOUNT_EXTENSION_MODELS above).
    entries = [(spec.key, spec.model_label, spec.field_name) for spec in ACCOUNT_RELATIONS]
    for model_label, skip in ACCOUNT_EXTENSION_MODELS:
        field_name = _extension_join_field(model_label, skip)
        entries.append((f"{model_label}.{field_name}", model_label, field_name))
    return entries


def extension_relation_keys() -> frozenset[str]:
    """Evidence keys that belong to an ``ACCOUNT_EXTENSION_MODELS`` row rather
    than to a named ``ACCOUNT_RELATIONS`` spec.

    These rows are the account's own storage, not a dependent relation: a
    merge is allowed to *create* one where a user had none yet (the same
    backfill ``courses.LearnerProfile``/``accounts_ext.IdentityState`` do
    outside a merge), so a caller comparing before/after evidence for these
    keys should check that nothing already present was lost (a subset
    check), not that the row set is byte-identical the way it is for every
    other, purely-reparented relation.
    """
    return frozenset(
        f"{model_label}.{_extension_join_field(model_label, skip)}"
        for model_label, skip in ACCOUNT_EXTENSION_MODELS
    )


def append_only_relation_keys() -> frozenset[str]:
    """Every evidence key where a merge is allowed to *add* a row.

    The extension tables (``extension_relation_keys``) and
    ``accounts_ext.AccountIdentityAlias.survivor`` (``"merge_ledger_append_only"``
    handling) both grow as a normal, expected side effect of the very apply
    whose evidence is being checked -- a backfilled extension row, or the one
    new alias row every successful merge creates. A caller checking before/
    after evidence for one of these keys should require a subset (nothing
    already there was lost), not byte-identical equality.
    """
    return extension_relation_keys() | {
        spec.key for spec in ACCOUNT_RELATIONS if spec.handling == "merge_ledger_append_only"
    }


def _current_aliases(alias_overrides: dict[int, int] | None) -> dict[int, int]:
    from accounts_ext.models import AccountIdentityAlias

    aliases = dict(
        AccountIdentityAlias.objects.values_list(
            "source_user_id",
            "survivor_id",
        )
    )
    aliases.update(alias_overrides or {})
    return aliases


def relationship_evidence(
    *,
    alias_overrides: dict[int, int] | None = None,
) -> tuple[dict[str, int], dict[str, str]]:
    aliases = _current_aliases(alias_overrides)
    counts: dict[str, int] = {}
    checksums: dict[str, str] = {}
    for key, model_label, field_name in _relations_and_keys():
        logical_rows = _relation_logical_rows(
            model_label=model_label,
            field_name=field_name,
            aliases=aliases,
        )
        counts[key] = len(logical_rows)
        checksums[key] = sha256_text(canonical_json({"rows": logical_rows}))
    return counts, checksums


def relationship_row_identities(
    *,
    alias_overrides: dict[int, int] | None = None,
) -> dict[str, frozenset[tuple[str, int]]]:
    """The same rows ``relationship_evidence`` counts and checksums, as sets.

    A checksum proves two runs saw the identical row set, but cannot answer
    whether one is a *subset* of the other -- the question a caller has to
    ask for an ``extension_relation_keys()`` key, where a merge is allowed to
    add a row (the account gained a previously-missing extension row) but
    never allowed to lose one.
    """
    aliases = _current_aliases(alias_overrides)
    return {
        key: frozenset(
            (row_id, owner_id)
            for row_id, owner_id in _relation_logical_rows(
                model_label=model_label,
                field_name=field_name,
                aliases=aliases,
            )
        )
        for key, model_label, field_name in _relations_and_keys()
    }
