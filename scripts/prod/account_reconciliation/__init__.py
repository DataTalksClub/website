"""One-time account-merge reconciliation: dry-run, apply, rollback-check.

Relocated out of ``accounts/`` (was ``accounts/reconciliation.py``, wrapped by
the now-retired ``accounts.management.commands.reconcile_accounts``).  Nothing
in the live application imports this package -- it exists only for
``scripts/prod/import_account_reconciliation.py`` to call once, against a
reviewed mapping document, during the production data migration.  See
``_docs/runbooks/production-data-migration.md`` and
``_docs/runbooks/ingest-script-inventory.md`` (CMP learner accounts, 4.3) for
the full journey.

What stays permanent, in ``accounts/``, and why this package does not own it
------------------------------------------------------------------------------

``accounts.models.CustomUser.IdentityState.ABSORBED``, ``AccountIdentityAlias``
and ``AccountIdentityQuarantine`` are not reconciliation-only bookkeeping: an
absorbed account's session is resolved to its survivor on every request for
the life of the application (``accounts.identity_resolution``,
``accounts.middleware.DurableAccountSessionMiddleware``), and
``AccountIdentityQuarantine`` is also written outside this package entirely,
by ``accounts.auth.ConsolidatingSocialAccountAdapter`` when a live sign-in
hits an unresolved identity collision.  Those stay exactly where they are.

``accounts.models.AccountReconciliationRun`` is different from those in that
nothing at request time ever reads it -- but it still has to be a real Django
model living in an installed app, because that is the only way for it to get
a migration and cheap FK-free lookups against ``CustomUser``.  ``scripts/prod``
is plain scripts, not a Django app, so it cannot host a model at all.  Moving
the *table* out of ``accounts`` is not possible without inventing a second
app for one table; what *does* move, and does here, is every line of
*business logic* that reads or writes it -- dry-run, apply, rollback-check,
and every conflict/quarantine helper they use.  ``accounts/models.py`` keeps
only the column and constraint definitions, documented there as import
tooling nothing at request time touches.

Why ``AccountReconciliationRun`` stays a database row rather than becoming
ingestion-scoped script state (a JSON file, an in-memory dict)
------------------------------------------------------------------------------

This package needs two properties from the same record, at once, for the
same irreversible operation (the migration runbook's own words: "account
reconciliation is the step in this whole migration with no rollback"):

1. **Idempotency** -- replaying the same ``(snapshot_id, mapping_checksum,
   mode=apply)`` returns the first run's cached result rather than
   re-merging.  A JSON file could do this alone.
2. **Concurrency safety** -- two simultaneous applies of the same mapping
   must result in exactly one merge, with the loser safely receiving the
   winner's cached result, never a double merge and never an unhandled
   ``IntegrityError``.  A JSON file cannot honestly give this.  Two processes
   racing a read-then-write against the same file cannot be made atomic
   without inventing cross-process file locking -- and that locking would
   itself need to be exactly as correct as a database's own compare-and-swap
   to be worth trusting for a merge that cannot be undone.  A database
   ``UniqueConstraint`` gives that compare-and-swap for free, enforced by the
   engine, not by this package's own care.

Given both properties have to come from the same record for an operation
this brief explicitly calls out as having no rollback, this package keeps
``AccountReconciliationRun`` as the real table it already was, and relies on
``apply_reviewed_mapping``'s existing ``IntegrityError`` handling (below) --
proven by a real two-thread test, not by reading the code -- rather than
rebuild a weaker version of the same guarantee in a file.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from allauth.account.models import EmailAddress  # type: ignore[import-untyped]
from allauth.socialaccount.models import SocialAccount  # type: ignore[import-untyped]
from django.apps import apps
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from accounts.identity_inventory import ACCOUNT_RELATIONS, relationship_evidence
from accounts.identity_values import (
    canonical_json,
    normalize_account_email,
    sha256_text,
    validate_safe_reference,
    validate_snapshot_id,
)
from accounts.models import (
    AccountIdentityAlias,
    AccountIdentityQuarantine,
    AccountReconciliationRun,
    CustomUser,
)
from course_management.observability import record_event

PROFILE_FIELDS = (
    "first_name",
    "last_name",
    "certificate_name",
    "country",
    "region",
    "registration_role",
    "github_url",
    "linkedin_url",
    "personal_website_url",
    "about_me",
    "dark_mode",
    "preferred_timezone",
)
OWNERSHIP_EVIDENCE = frozenset(
    {
        "verified_normalized_email",
        "manual_verified_ownership",
    }
)
FIELD_DECISIONS = frozenset({"source", "survivor"})
AUTHORITY_DECISION = "survivor_only"
RECONCILIATION_SCHEMA_VERSION = 1


class ReconciliationError(ValueError):
    pass


class ReconciliationBlocked(ReconciliationError):
    def __init__(self, conflicts: tuple[dict[str, Any], ...]) -> None:
        self.conflicts = conflicts
        super().__init__("account reconciliation requires quarantine review")


class _ApplyPreconditionBlocked(Exception):
    def __init__(self, conflicts: tuple[dict[str, Any], ...]) -> None:
        self.conflicts = conflicts
        super().__init__("account reconciliation preconditions changed")


@dataclass(frozen=True, slots=True)
class ReviewedMapping:
    source_user_id: int
    survivor_user_id: int
    ownership_evidence: tuple[str, ...]
    field_decisions: dict[str, str]
    authority_decision: str


@dataclass(frozen=True, slots=True)
class MappingPlan:
    snapshot_id: str
    review_reference: str
    mappings: tuple[ReviewedMapping, ...]
    checksum: str
    canonical_document: dict[str, Any]


class _UnionFind:
    def __init__(self, user_ids: list[int]) -> None:
        self.parent = {user_id: user_id for user_id in user_ids}

    def find(self, user_id: int) -> int:
        parent = self.parent[user_id]
        if parent != user_id:
            self.parent[user_id] = self.find(parent)
        return self.parent[user_id]

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        low, high = sorted((left_root, right_root))
        self.parent[high] = low


def _identity_token(kind: str, value: str) -> str:
    return f"{kind}:{sha256_text(value)}"


def _candidate_tokens() -> tuple[dict[int, set[str]], dict[str, set[int]]]:
    tokens_by_user: dict[int, set[str]] = defaultdict(set)
    users_by_token: dict[str, set[int]] = defaultdict(set)

    users = CustomUser.objects.order_by("pk").only(
        "pk",
        "username",
        "email",
        "normalized_email",
    )
    for user in users:
        email = user.normalized_email or normalize_account_email(user.email)
        username = (
            user.username.strip().casefold()
            if isinstance(user.username, str) and user.username.strip()
            else None
        )
        if email is not None:
            tokens_by_user[user.pk].add(_identity_token("normalized_email", email))
            tokens_by_user[user.pk].add(_identity_token("identifier", email))
        if username is not None:
            tokens_by_user[user.pk].add(_identity_token("username", username))
            tokens_by_user[user.pk].add(_identity_token("identifier", username))

    for user_id, email in EmailAddress.objects.filter(verified=True).values_list(
        "user_id",
        "email",
    ):
        normalized = normalize_account_email(email)
        if normalized is not None:
            tokens_by_user[user_id].add(_identity_token("verified_email", normalized))

    for user_id, provider, uid in SocialAccount.objects.values_list(
        "user_id",
        "provider",
        "uid",
    ):
        provider_key = str(provider)[:32]
        tokens_by_user[user_id].add(
            _identity_token(
                f"provider_uid:{provider_key}",
                str(uid),
            )
        )

    for user_id, tokens in tokens_by_user.items():
        for token in tokens:
            users_by_token[token].add(user_id)
    return tokens_by_user, users_by_token


def _authority_signature(user: CustomUser) -> tuple[Any, ...]:
    return (
        user.is_active,
        user.is_staff,
        user.is_superuser,
        user.role,
        tuple(user.groups.order_by("pk").values_list("pk", flat=True)),
        tuple(user.user_permissions.order_by("pk").values_list("pk", flat=True)),
    )


def _provider_conflict(user_ids: tuple[int, ...]) -> bool:
    providers_by_user: dict[int, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    rows = SocialAccount.objects.filter(user_id__in=user_ids).values_list(
        "user_id",
        "provider",
        "uid",
    )
    for user_id, provider, uid in rows:
        providers_by_user[user_id][provider].add(sha256_text(str(uid)))
    providers = {provider for values in providers_by_user.values() for provider in values}
    for provider in providers:
        uid_sets = [values[provider] for values in providers_by_user.values() if provider in values]
        if len(uid_sets) > 1 and len(set().union(*uid_sets)) > 1:
            return True
    return False


def _relationship_collision_codes(user_ids: tuple[int, ...]) -> list[str]:
    codes: list[str] = []
    for model_label, field_name, scope_name in (
        ("courses.Enrollment", "student", "course_id"),
        ("courses.UserWrappedStatistics", "user", "wrapped_id"),
    ):
        model = apps.get_model(model_label)
        scopes: dict[Any, set[int]] = defaultdict(set)
        rows = model._base_manager.filter(**{f"{field_name}_id__in": user_ids}).values_list(
            f"{field_name}_id", scope_name
        )
        for user_id, scope_id in rows:
            scopes[scope_id].add(user_id)
        if any(len(owners) > 1 for owners in scopes.values()):
            codes.append(f"{model_label.casefold()}_collision")

    Vote = apps.get_model("courses.ProjectVote")
    vote_scopes: dict[Any, set[int]] = defaultdict(set)
    for user_id, submission_id in Vote.objects.filter(voter_id__in=user_ids).values_list(
        "voter_id", "submission_id"
    ):
        vote_scopes[submission_id].add(user_id)
    if any(len(owners) > 1 for owners in vote_scopes.values()):
        codes.append("courses.projectvote_collision")
    return codes


def dry_run_reconciliation(*, snapshot_id: str) -> dict[str, Any]:
    snapshot = validate_snapshot_id(snapshot_id)
    users = tuple(CustomUser.objects.order_by("pk"))
    user_ids = [user.pk for user in users]
    users_by_id = {user.pk: user for user in users}
    tokens_by_user, users_by_token = _candidate_tokens()
    union_find = _UnionFind(user_ids)
    for owners in users_by_token.values():
        owner_ids = sorted(owners)
        for owner_id in owner_ids[1:]:
            union_find.union(owner_ids[0], owner_id)

    components: dict[int, list[int]] = defaultdict(list)
    for user_id in user_ids:
        components[union_find.find(user_id)].append(user_id)

    candidate_groups = []
    for component_ids in sorted(components.values()):
        if len(component_ids) < 2:
            continue
        ids = tuple(sorted(component_ids))
        shared_tokens = sorted(
            token
            for token in set().union(*(tokens_by_user[user_id] for user_id in ids))
            if len(users_by_token[token].intersection(ids)) > 1
        )
        evidence = sorted({token.partition(":")[0] for token in shared_tokens})
        risks = _relationship_collision_codes(ids)
        signatures = {_authority_signature(users_by_id[user_id]) for user_id in ids}
        if len(signatures) > 1:
            risks.append("authority_collision")
        if _provider_conflict(ids):
            risks.append("provider_uid_conflict")
        fingerprint = sha256_text(
            canonical_json(
                {
                    "snapshot_id": snapshot,
                    "source_user_ids": list(ids),
                    "evidence": evidence,
                    "risk_codes": sorted(risks),
                }
            )
        )
        candidate_groups.append(
            {
                "source_user_ids": list(ids),
                "fingerprint": fingerprint,
                "evidence": evidence,
                "risk_codes": sorted(risks),
                "disposition": "review_required",
                "automatic_survivor": None,
            }
        )

    relation_counts, relation_checksums = relationship_evidence()
    report = {
        "schema_version": "account-reconciliation-dry-run-v1",
        "snapshot_id": snapshot,
        "write_performed": False,
        "outbound_side_effects": False,
        "source_account_count": len(users),
        "candidate_group_count": len(candidate_groups),
        "candidate_groups": candidate_groups,
        "unchanged_source_user_ids": user_ids,
        "relationship_counts": relation_counts,
        "relationship_checksums": relation_checksums,
        "survivor_policy": "reviewed_mapping_only",
        "newest_last_login_authoritative": False,
    }
    report["report_checksum"] = sha256_text(canonical_json(report))
    return report


def parse_mapping_document(document: dict[str, Any]) -> MappingPlan:
    if not isinstance(document, dict):
        raise ReconciliationError("mapping document must be a JSON object")
    if document.get("schema_version") != RECONCILIATION_SCHEMA_VERSION:
        raise ReconciliationError("mapping schema version is unsupported")
    try:
        snapshot_id = validate_snapshot_id(document["snapshot_id"])
        review_reference = validate_safe_reference(
            document["review_reference"],
            label="review reference",
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ReconciliationError(str(error)) from error
    raw_mappings = document.get("mappings")
    if not isinstance(raw_mappings, list):
        raise ReconciliationError("mappings must be a list")

    parsed: list[ReviewedMapping] = []
    for raw in raw_mappings:
        if not isinstance(raw, dict):
            raise ReconciliationError("each mapping must be an object")
        try:
            source_user_id = int(raw["source_user_id"])
            survivor_user_id = int(raw["survivor_user_id"])
        except (KeyError, TypeError, ValueError) as error:
            raise ReconciliationError("mapping IDs must be integers") from error
        if source_user_id < 1 or survivor_user_id < 1:
            raise ReconciliationError("mapping IDs must be positive")
        raw_evidence = raw.get("ownership_evidence")
        if not isinstance(raw_evidence, list) or not raw_evidence:
            raise ReconciliationError("ownership evidence is required")
        evidence = tuple(sorted(set(raw_evidence)))
        if not set(evidence).issubset(OWNERSHIP_EVIDENCE):
            raise ReconciliationError("ownership evidence is unsupported")
        raw_decisions = raw.get("field_decisions", {})
        if not isinstance(raw_decisions, dict):
            raise ReconciliationError("field decisions must be an object")
        decisions = {str(key): str(value) for key, value in raw_decisions.items()}
        if not set(decisions).issubset(PROFILE_FIELDS):
            raise ReconciliationError("field decision is not allowlisted")
        if not set(decisions.values()).issubset(FIELD_DECISIONS):
            raise ReconciliationError("field decision value is unsupported")
        authority_decision = str(raw.get("authority_decision", ""))
        if authority_decision not in {"", AUTHORITY_DECISION}:
            raise ReconciliationError("authority decision is unsupported")
        parsed.append(
            ReviewedMapping(
                source_user_id=source_user_id,
                survivor_user_id=survivor_user_id,
                ownership_evidence=evidence,
                field_decisions=decisions,
                authority_decision=authority_decision,
            )
        )

    parsed.sort(key=lambda item: item.source_user_id)
    source_ids = [mapping.source_user_id for mapping in parsed]
    if len(source_ids) != len(set(source_ids)):
        raise ReconciliationError("each source account may appear only once")
    survivor_ids = {mapping.survivor_user_id for mapping in parsed}
    if set(source_ids).intersection(survivor_ids):
        raise ReconciliationError("mapping chains and cycles are forbidden")

    canonical_document = {
        "schema_version": RECONCILIATION_SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "review_reference": review_reference,
        "mappings": [
            {
                "source_user_id": mapping.source_user_id,
                "survivor_user_id": mapping.survivor_user_id,
                "ownership_evidence": list(mapping.ownership_evidence),
                "field_decisions": dict(sorted(mapping.field_decisions.items())),
                "authority_decision": mapping.authority_decision,
            }
            for mapping in parsed
        ],
    }
    return MappingPlan(
        snapshot_id=snapshot_id,
        review_reference=review_reference,
        mappings=tuple(parsed),
        checksum=sha256_text(canonical_json(canonical_document)),
        canonical_document=canonical_document,
    )


def _verified_email_set(user_id: int) -> set[str]:
    values = EmailAddress.objects.filter(
        user_id=user_id,
        verified=True,
    ).values_list("email", flat=True)
    return {
        normalized for value in values if (normalized := normalize_account_email(value)) is not None
    }


def _mapping_conflicts(
    mapping: ReviewedMapping,
    *,
    users_by_id: dict[int, CustomUser],
) -> list[str]:
    source = users_by_id.get(mapping.source_user_id)
    survivor = users_by_id.get(mapping.survivor_user_id)
    if source is None or survivor is None:
        return ["account_missing"]
    if source.pk == survivor.pk:
        return ["source_equals_survivor"]
    existing_alias = AccountIdentityAlias.objects.filter(source_user_id=source.pk).first()
    if existing_alias is not None:
        if existing_alias.survivor_id == survivor.pk:
            return []
        return ["source_alias_conflict"]
    if survivor.identity_state in {
        CustomUser.IdentityState.ABSORBED,
        CustomUser.IdentityState.QUARANTINED,
    }:
        return ["survivor_unavailable"]
    if not survivor.is_active:
        return ["survivor_inactive"]

    source_verified = _verified_email_set(source.pk)
    survivor_verified = _verified_email_set(survivor.pk)
    if not source_verified or not survivor_verified:
        return ["verified_email_evidence_missing"]
    if (
        "verified_normalized_email" in mapping.ownership_evidence
        and not source_verified.intersection(survivor_verified)
    ):
        return ["verified_normalized_email_mismatch"]

    conflicts = _relationship_collision_codes((source.pk, survivor.pk))
    if _provider_conflict((source.pk, survivor.pk)):
        conflicts.append("provider_uid_conflict")
    authority_differs = _authority_signature(source) != _authority_signature(survivor)
    if authority_differs and mapping.authority_decision != AUTHORITY_DECISION:
        conflicts.append("authority_decision_required")
    if not source.is_active:
        conflicts.append("source_security_disabled")

    for field in PROFILE_FIELDS:
        if getattr(source, field) == getattr(survivor, field):
            continue
        if field not in mapping.field_decisions:
            conflicts.append(f"field_decision_required:{field}")
    return sorted(set(conflicts))


def _quarantine_mapping_conflicts(
    *,
    plan: MappingPlan,
    conflicts: tuple[dict[str, Any], ...],
) -> None:
    user_ids = {
        conflict[user_id_key]
        for conflict in conflicts
        for user_id_key in ("source_user_id", "survivor_user_id")
    }
    try:
        with transaction.atomic():
            users_by_id = {user.pk: user for user in CustomUser.objects.filter(pk__in=user_ids)}
            audit_contexts = tuple(
                _denied_merge_audit_context(
                    source=users_by_id.get(conflict["source_user_id"]),
                    survivor=users_by_id.get(conflict["survivor_user_id"]),
                )
                for conflict in conflicts
            )
            for conflict, audit_context in zip(
                conflicts,
                audit_contexts,
                strict=True,
            ):
                source_user_id = conflict["source_user_id"]
                survivor_user_id = conflict["survivor_user_id"]
                reasons = conflict["reason_codes"]
                fingerprint = sha256_text(
                    canonical_json(
                        {
                            "snapshot_id": plan.snapshot_id,
                            "mapping_checksum": plan.checksum,
                            "source_user_ids": [
                                source_user_id,
                                survivor_user_id,
                            ],
                            "reason_codes": reasons,
                        }
                    )
                )
                AccountIdentityQuarantine.objects.get_or_create(
                    fingerprint=fingerprint,
                    defaults={
                        "source_snapshot_id": plan.snapshot_id,
                        "source_user_ids": [
                            source_user_id,
                            survivor_user_id,
                        ],
                        "reason_codes": reasons,
                    },
                )
                _record_merge_audit(
                    action="accounts.identity.merge_denied",
                    outcome="denied",
                    source_user_id=source_user_id,
                    survivor_user_id=survivor_user_id,
                    snapshot_id=plan.snapshot_id,
                    mapping_checksum=plan.checksum,
                    reason_codes=reasons,
                    audit_context=audit_context,
                )
    except IntegrityError:
        raise ReconciliationBlocked(conflicts) from None

    for conflict in conflicts:
        reasons = conflict["reason_codes"]
        record_event(
            "auth.account_merge_quarantined",
            properties={
                "reason_count": len(reasons),
                "source_count": 1,
            },
        )


def _is_valid_audit_authority(user: CustomUser | None) -> bool:
    return bool(
        user is not None
        and user.is_active
        and user.identity_state
        in {
            CustomUser.IdentityState.ACTIVE,
            CustomUser.IdentityState.LEGACY,
        }
    )


def _denied_merge_audit_context(
    *,
    source: CustomUser | None,
    survivor: CustomUser | None,
):
    from core.audit import AuditWriteContext

    actor = next(
        (candidate for candidate in (source, survivor) if _is_valid_audit_authority(candidate)),
        None,
    )
    if actor is None:
        context = AuditWriteContext(
            actor_ref="system:account-reconciliation",
        )
    else:
        context = AuditWriteContext(
            actor_id=actor.pk,
            actor_ref=f"user:{actor.pk}",
        )
    return context.validated()


def _successful_merge_audit_context(*, survivor: CustomUser):
    from core.audit import AuditWriteContext

    if not _is_valid_audit_authority(survivor):
        raise IntegrityError("merge audit authority is unavailable")
    return AuditWriteContext(
        actor_id=survivor.pk,
        actor_ref=f"user:{survivor.pk}",
    ).validated()


def _profile_changes(
    *,
    source: CustomUser,
    survivor: CustomUser,
    mapping: ReviewedMapping,
) -> list[str]:
    update_fields: list[str] = []
    for field, decision in mapping.field_decisions.items():
        if decision != "source":
            continue
        setattr(survivor, field, getattr(source, field))
        update_fields.append(field)
    return update_fields


def _reparent_relations(*, source_id: int, survivor_id: int) -> None:
    for spec in ACCOUNT_RELATIONS:
        if spec.handling != "reparent":
            continue
        model = apps.get_model(spec.model_label)
        model._base_manager.filter(**{f"{spec.field_name}_id": source_id}).update(
            **{f"{spec.field_name}_id": survivor_id}
        )


def _reparent_verified_identity_relations(
    *,
    source_id: int,
    survivor_id: int,
) -> None:
    source_addresses = EmailAddress.objects.filter(
        user_id=source_id,
        verified=True,
    )
    if EmailAddress.objects.filter(user_id=survivor_id, primary=True).exists():
        source_addresses.filter(primary=True).update(primary=False)
    source_addresses.update(user_id=survivor_id)
    SocialAccount.objects.filter(user_id=source_id).update(user_id=survivor_id)


def _disable_source_management_principals(source_id: int) -> None:
    Principal = apps.get_model("management_auth.APIPrincipal")
    Principal.objects.filter(user_id=source_id, is_active=True).update(
        is_active=False,
        revision=F("revision") + 1,
        updated_at=timezone.now(),
    )


def _record_merge_audit(
    *,
    action: str,
    outcome: str,
    source_user_id: int,
    survivor_user_id: int,
    snapshot_id: str,
    mapping_checksum: str,
    reason_codes: list[str] | tuple[str, ...] = (),
    audit_context=None,
) -> None:
    from core.audit import record_audit_event

    record_audit_event(
        action=action,
        target_type="accounts.identity",
        outcome=outcome,
        context=audit_context,
        changes={"identity_state": "absorbed" if outcome == "succeeded" else "unchanged"},
        metadata={
            "source_user_id": source_user_id,
            "survivor_user_id": survivor_user_id,
            "snapshot_id": snapshot_id,
            "mapping_checksum": mapping_checksum,
            "reason_codes": list(reason_codes),
        },
    )


def _apply_one_mapping(
    *,
    mapping: ReviewedMapping,
    plan: MappingPlan,
) -> None:
    existing_alias = AccountIdentityAlias.objects.filter(
        source_user_id=mapping.source_user_id
    ).first()
    if existing_alias is not None:
        if (
            existing_alias.survivor_id == mapping.survivor_user_id
            and existing_alias.mapping_checksum == plan.checksum
        ):
            return
        raise ReconciliationError("source alias changed across reconciliation runs")

    source = CustomUser.objects.get(pk=mapping.source_user_id)
    survivor = CustomUser.objects.get(pk=mapping.survivor_user_id)
    source_snapshot = {
        "email": source.email,
        "normalized_email": source.normalized_email,
        "identity_state": source.identity_state,
        "is_active": source.is_active,
        "is_staff": source.is_staff,
        "is_superuser": source.is_superuser,
        "role": source.role,
        **{field: getattr(source, field) for field in PROFILE_FIELDS},
    }
    survivor_snapshot = {
        "email": survivor.email,
        "normalized_email": survivor.normalized_email,
        "identity_state": survivor.identity_state,
        "is_active": survivor.is_active,
        "is_staff": survivor.is_staff,
        "is_superuser": survivor.is_superuser,
        "role": survivor.role,
        **{field: getattr(survivor, field) for field in PROFILE_FIELDS},
    }
    update_fields = _profile_changes(
        source=source,
        survivor=survivor,
        mapping=mapping,
    )
    survivor_email = normalize_account_email(survivor.email)
    if survivor_email is None or survivor_email not in _verified_email_set(survivor.pk):
        raise IntegrityError("survivor verified email changed during apply")
    source_claimed = CustomUser.objects.filter(
        pk=source.pk,
        **source_snapshot,
    ).update(identity_state=CustomUser.IdentityState.ABSORBED)
    if source_claimed != 1:
        raise IntegrityError("source identity changed during apply")

    survivor_updates = {field: getattr(survivor, field) for field in update_fields}
    survivor_updates.update(
        normalized_email=survivor_email,
        identity_state=CustomUser.IdentityState.ACTIVE,
    )
    survivor_claimed = CustomUser.objects.filter(
        pk=survivor.pk,
        **survivor_snapshot,
    ).update(**survivor_updates)
    if survivor_claimed != 1:
        raise IntegrityError("survivor identity changed during apply")

    _reparent_relations(
        source_id=source.pk,
        survivor_id=survivor.pk,
    )
    _reparent_verified_identity_relations(
        source_id=source.pk,
        survivor_id=survivor.pk,
    )
    _disable_source_management_principals(source.pk)
    AccountIdentityAlias.objects.create(
        source_user_id=source.pk,
        survivor=survivor,
        source_snapshot_id=plan.snapshot_id,
        mapping_checksum=plan.checksum,
        review_reference=plan.review_reference,
    )


def apply_reviewed_mapping(plan: MappingPlan) -> dict[str, Any]:
    existing_run = AccountReconciliationRun.objects.filter(
        source_snapshot_id=plan.snapshot_id,
        mapping_checksum=plan.checksum,
        mode=AccountReconciliationRun.Mode.APPLY,
    ).first()
    if existing_run is not None:
        _validated_aliases(plan)
        return {
            "schema_version": "account-reconciliation-apply-v1",
            "snapshot_id": plan.snapshot_id,
            "mapping_checksum": plan.checksum,
            "idempotent_replay": True,
            "run_id": str(existing_run.id),
            "report_checksum": existing_run.report_checksum,
            "outbound_side_effects": False,
        }

    users_by_id = {
        user.pk: user
        for user in CustomUser.objects.filter(
            pk__in={
                user_id
                for mapping in plan.mappings
                for user_id in (
                    mapping.source_user_id,
                    mapping.survivor_user_id,
                )
            }
        )
    }
    conflicts = tuple(
        {
            "source_user_id": mapping.source_user_id,
            "survivor_user_id": mapping.survivor_user_id,
            "reason_codes": reasons,
        }
        for mapping in plan.mappings
        if (
            reasons := _mapping_conflicts(
                mapping,
                users_by_id=users_by_id,
            )
        )
    )
    if conflicts:
        _quarantine_mapping_conflicts(plan=plan, conflicts=conflicts)
        raise ReconciliationBlocked(conflicts)

    prospective_aliases = {
        mapping.source_user_id: mapping.survivor_user_id for mapping in plan.mappings
    }
    before_counts, before_checksums = relationship_evidence(alias_overrides=prospective_aliases)
    source_account_count = CustomUser.objects.count()

    try:
        with transaction.atomic():
            current_users_by_id = {
                user.pk: user
                for user in CustomUser.objects.filter(
                    pk__in={
                        user_id
                        for mapping in plan.mappings
                        for user_id in (
                            mapping.source_user_id,
                            mapping.survivor_user_id,
                        )
                    }
                )
            }
            current_conflicts = tuple(
                {
                    "source_user_id": mapping.source_user_id,
                    "survivor_user_id": mapping.survivor_user_id,
                    "reason_codes": reasons,
                }
                for mapping in plan.mappings
                if (
                    reasons := _mapping_conflicts(
                        mapping,
                        users_by_id=current_users_by_id,
                    )
                )
            )
            if current_conflicts:
                raise _ApplyPreconditionBlocked(current_conflicts)

            success_audit_contexts = {
                mapping.source_user_id: _successful_merge_audit_context(
                    survivor=current_users_by_id[mapping.survivor_user_id]
                )
                for mapping in plan.mappings
            }

            for mapping in plan.mappings:
                _apply_one_mapping(mapping=mapping, plan=plan)
            reconciled_counts, reconciled_checksums = relationship_evidence()
            if before_counts != reconciled_counts or before_checksums != reconciled_checksums:
                raise IntegrityError("account relationship reconciliation changed logical evidence")

            for mapping in plan.mappings:
                _record_merge_audit(
                    action="accounts.identity.merge_succeeded",
                    outcome="succeeded",
                    source_user_id=mapping.source_user_id,
                    survivor_user_id=mapping.survivor_user_id,
                    snapshot_id=plan.snapshot_id,
                    mapping_checksum=plan.checksum,
                    audit_context=success_audit_contexts[mapping.source_user_id],
                )
            after_counts, after_checksums = relationship_evidence()

            report: dict[str, Any] = {
                "schema_version": "account-reconciliation-apply-v1",
                "snapshot_id": plan.snapshot_id,
                "mapping_checksum": plan.checksum,
                "idempotent_replay": False,
                "outbound_side_effects": False,
                "source_account_count": source_account_count,
                "survivor_account_count": (source_account_count - len(plan.mappings)),
                "applied_source_user_ids": [mapping.source_user_id for mapping in plan.mappings],
                "survivor_user_ids": sorted(
                    {mapping.survivor_user_id for mapping in plan.mappings}
                ),
                "relationship_counts": after_counts,
                "relationship_checksums": after_checksums,
                "alias_count": AccountIdentityAlias.objects.count(),
                "quarantine_count": (
                    AccountIdentityQuarantine.objects.filter(
                        status=AccountIdentityQuarantine.Status.OPEN
                    ).count()
                ),
                "privilege_union": False,
                "consent_union": False,
            }
            report_checksum = sha256_text(canonical_json(report))
            run = AccountReconciliationRun.objects.create(
                source_snapshot_id=plan.snapshot_id,
                mapping_checksum=plan.checksum,
                mode=AccountReconciliationRun.Mode.APPLY,
                source_account_count=source_account_count,
                survivor_account_count=report["survivor_account_count"],
                alias_count=report["alias_count"],
                quarantine_count=report["quarantine_count"],
                relationship_counts=after_counts,
                relationship_checksums=after_checksums,
                report_checksum=report_checksum,
            )
            report["run_id"] = str(run.id)
            report["report_checksum"] = report_checksum
    except _ApplyPreconditionBlocked as error:
        _quarantine_mapping_conflicts(
            plan=plan,
            conflicts=error.conflicts,
        )
        raise ReconciliationBlocked(error.conflicts) from None
    except IntegrityError:
        # Either a real concurrent apply of this exact mapping won the race
        # (the AccountReconciliationRun unique constraint is the compare-
        # and-swap) or a genuine data-integrity refusal happened inside the
        # transaction above.  Distinguish by re-querying: if a run for this
        # snapshot/checksum/mode now exists, this was the race's loser and
        # gets the winner's cached result -- never a second merge attempt.
        concurrent_run = AccountReconciliationRun.objects.filter(
            source_snapshot_id=plan.snapshot_id,
            mapping_checksum=plan.checksum,
            mode=AccountReconciliationRun.Mode.APPLY,
        ).first()
        if concurrent_run is not None:
            _validated_aliases(plan)
            return {
                "schema_version": "account-reconciliation-apply-v1",
                "snapshot_id": plan.snapshot_id,
                "mapping_checksum": plan.checksum,
                "idempotent_replay": True,
                "run_id": str(concurrent_run.id),
                "report_checksum": concurrent_run.report_checksum,
                "outbound_side_effects": False,
            }
        integrity_conflicts = tuple(
            {
                "source_user_id": mapping.source_user_id,
                "survivor_user_id": mapping.survivor_user_id,
                "reason_codes": ["reconciliation_integrity_conflict"],
            }
            for mapping in plan.mappings
        )
        _quarantine_mapping_conflicts(
            plan=plan,
            conflicts=integrity_conflicts,
        )
        raise ReconciliationBlocked(integrity_conflicts) from None

    for mapping in plan.mappings:
        record_event(
            "auth.account_merge_succeeded",
            user=users_by_id[mapping.survivor_user_id],
            properties={
                "account_created": False,
                "source_count": 1,
            },
        )
    return report


def _validated_aliases(plan: MappingPlan) -> tuple[AccountIdentityAlias, ...]:
    aliases = tuple(
        AccountIdentityAlias.objects.filter(
            source_snapshot_id=plan.snapshot_id,
            mapping_checksum=plan.checksum,
        ).order_by("source_user_id")
    )
    expected = {mapping.source_user_id: mapping.survivor_user_id for mapping in plan.mappings}
    actual = {alias.source_user_id: alias.survivor_id for alias in aliases}
    if actual != expected:
        raise ReconciliationError("rollback alias evidence is incomplete")
    for source_user_id, survivor_user_id in expected.items():
        source = CustomUser.objects.filter(pk=source_user_id).first()
        survivor = CustomUser.objects.filter(pk=survivor_user_id).first()
        if (
            source is None
            or source.identity_state != CustomUser.IdentityState.ABSORBED
            or survivor is None
            or not survivor.is_active
            or survivor.identity_state != CustomUser.IdentityState.ACTIVE
        ):
            raise ReconciliationError("rollback identity state is unsafe")
    return aliases


def validate_rollback_window(plan: MappingPlan) -> dict[str, Any]:
    aliases = _validated_aliases(plan)
    counts, checksums = relationship_evidence()
    report = {
        "schema_version": "account-reconciliation-rollback-check-v1",
        "snapshot_id": plan.snapshot_id,
        "mapping_checksum": plan.checksum,
        "source_rows_retained": True,
        "aliases_retained": True,
        "relationships_reversed": False,
        "sessions_globally_flushed": False,
        "post_cutover_writes_retained": True,
        "relationship_counts": counts,
        "relationship_checksums": checksums,
    }
    report_checksum = sha256_text(canonical_json(report))
    run, _created = AccountReconciliationRun.objects.get_or_create(
        source_snapshot_id=plan.snapshot_id,
        mapping_checksum=plan.checksum,
        mode=AccountReconciliationRun.Mode.ROLLBACK_CHECK,
        defaults={
            "source_account_count": CustomUser.objects.count(),
            "survivor_account_count": (CustomUser.objects.count() - len(plan.mappings)),
            "alias_count": len(aliases),
            "quarantine_count": AccountIdentityQuarantine.objects.filter(
                status=AccountIdentityQuarantine.Status.OPEN
            ).count(),
            "relationship_counts": counts,
            "relationship_checksums": checksums,
            "report_checksum": report_checksum,
        },
    )
    report["run_id"] = str(run.id)
    report["report_checksum"] = run.report_checksum
    _record_rollback_audit(plan=plan)
    record_event(
        "auth.rollback_verified",
        properties={"mapping_count": len(plan.mappings)},
    )
    return report


def _record_rollback_audit(*, plan: MappingPlan) -> None:
    from core.audit import AuditWriteContext, record_audit_event
    from core.models import AuditEvent

    record_audit_event(
        action="accounts.identity.rollback_verified",
        target_type="accounts.identity",
        outcome=AuditEvent.Outcome.SUCCEEDED,
        context=AuditWriteContext(),
        changes={"relationships_reversed": False},
        metadata={
            "snapshot_id": plan.snapshot_id,
            "mapping_checksum": plan.checksum,
            "mapping_count": len(plan.mappings),
        },
    )
