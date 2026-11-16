"""Shared, authorized management services for the HTML and JSON adapters."""

import hashlib
import json
import secrets
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from community_base.kernel.services import ServiceContext
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q

from core.audit import AuditWriteContext, record_audit_event
from core.idempotency import execute_idempotent, hash_idempotency_key
from core.models import AuditEvent
from management_auth.models import APIPrincipal

from . import services
from .models import Application, JoinObservation, Membership, MonitorState


@dataclass
class SlackManagementError(ValueError):
    status: int
    code: str
    message: str

    def __post_init__(self):
        super().__init__(self.message)


def _actor(context: ServiceContext, *, ban=False):
    reference = context.actor_ref or ""
    kind, _, identifier = reference.partition(":")
    actor: Any
    try:
        if kind == "user":
            actor = get_user_model().objects.get(pk=int(identifier))
        elif kind == "api_principal":
            actor = APIPrincipal.objects.select_related("user").get(pk=identifier)
        else:
            raise PermissionDenied
    except (ValueError, get_user_model().DoesNotExist, APIPrincipal.DoesNotExist):
        raise PermissionDenied("Slack management permission is required.") from None
    services.reviewer(actor, ban=ban)
    return actor


def _payload(payload, allowed):
    if not isinstance(payload, dict) or set(payload) - set(allowed):
        raise SlackManagementError(400, "invalid_fields", "Use only the documented fields.")


def _integer(value, *, minimum=1, maximum=2147483647):
    if isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) <= 10:
        value = int(value)
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise SlackManagementError(400, "invalid_identifier", "Use a valid bounded integer.")
    return value


def _page(payload):
    return (
        _integer(payload.get("after", 0), minimum=0),
        _integer(payload.get("limit", 50), maximum=100),
    )


def _uuid(value):
    if not isinstance(value, str):
        raise SlackManagementError(400, "invalid_identifier", "Use a canonical UUID identifier.")
    try:
        parsed = UUID(value)
    except ValueError:
        raise SlackManagementError(
            400, "invalid_identifier", "Use a canonical UUID identifier."
        ) from None
    if str(parsed) != value:
        raise SlackManagementError(400, "invalid_identifier", "Use a canonical UUID identifier.")
    return value


def _summary(application, *, questionnaire=False):
    value = {
        "id": str(application.public_id),
        "user_id": application.user_id,
        "state": application.state,
        "sponsor_id": str(application.sponsor.public_id)
        if application.sponsor is not None
        else None,
        "approval_source": application.approval_source,
    }
    if questionnaire:
        value.update(who=application.who, work=application.work, what=application.what)
    return value


def _audit(action, context, actor, *, metadata=None, target="slack-management"):
    principal_id = actor.pk if isinstance(actor, APIPrincipal) else None
    actor_id = actor.user_id if isinstance(actor, APIPrincipal) else actor.pk
    scope = f"{action}:{context.actor_ref}"
    record_audit_event(
        action=action,
        target_type="slack.management",
        target_label=target,
        outcome=AuditEvent.Outcome.SUCCEEDED,
        context=AuditWriteContext.from_service_context(
            context,
            actor_id=actor_id,
            api_principal_id=principal_id,
            idempotency_key_hash=(
                hash_idempotency_key(scope, context.idempotency_key)
                if context.idempotency_key
                else ""
            ),
        ),
        metadata=metadata or {},
    )


def _command(action, payload, context, actor, command):
    if not context.idempotency_key:
        raise SlackManagementError(400, "idempotency_required", "An idempotency key is required.")

    def effect():
        result = command()
        _audit(action, context, actor, metadata=result)
        return result

    result = execute_idempotent(
        scope=f"{action}:{context.actor_ref}",
        key=context.idempotency_key,
        request=payload,
        command=effect,
    )
    return {**result.value, "replayed": result.replayed}


def _ban_snapshot(member_id):
    affected = services.ban_scope(member_id)
    members = Membership.objects.filter(pk__in=affected)
    # The fingerprint contains only stable identifiers and domain state. Neither
    # questionnaire answers nor provider profile data enters a command or audit.
    snapshot = {
        "members": [
            [
                str(row.public_id),
                str(row.sponsor.public_id) if row.sponsor else None,
                row.banned_at.isoformat() if row.banned_at else None,
                row.user_id,
            ]
            for row in members.select_related("sponsor").order_by("public_id")
        ],
        "applications": [
            [
                str(row.public_id),
                row.state,
                str(row.sponsor.public_id) if row.sponsor else None,
                row.user_id,
            ]
            for row in Application.objects.filter(
                Q(user_id__in=members.values("user_id")) | Q(sponsor_id__in=affected)
            )
            .select_related("sponsor")
            .order_by("public_id")
        ],
    }
    fingerprint = hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return len(affected), fingerprint


def list_applications(payload, *, context: ServiceContext):
    actor = _actor(context)
    _payload(payload, {"after", "limit", "state"})
    after, limit = _page(payload)
    state = payload.get("state", Application.State.PENDING)
    if not isinstance(state, str) or state not in Application.State.values:
        raise SlackManagementError(400, "invalid_state", "Choose a documented application state.")
    rows = list(
        Application.objects.filter(pk__gt=after, state=state)
        .select_related("sponsor")
        .order_by("pk")[: limit + 1]
    )
    _audit("slack.applications.list", context, actor, metadata={"row_count": min(len(rows), limit)})
    return {
        "applications": [_summary(row, questionnaire=True) for row in rows[:limit]],
        "next_after": rows[limit - 1].pk if len(rows) > limit else None,
    }


def review_application(payload, *, context: ServiceContext):
    actor = _actor(context)
    _payload(payload, {"application_id", "action", "expected_state"})
    identifier = _uuid(payload.get("application_id"))
    action = payload.get("action")
    expected = payload.get("expected_state")
    if (
        not isinstance(action, str)
        or action not in {"approve", "reject"}
        or expected != Application.State.PENDING
    ):
        raise SlackManagementError(
            400, "invalid_review", "Review a pending application explicitly."
        )

    @transaction.atomic
    def effect():
        services._lock()
        application = Application.objects.select_for_update().filter(public_id=identifier).first()
        if application is None:
            raise SlackManagementError(404, "not_found", "The application was not found.")
        if application.state != expected:
            raise SlackManagementError(409, "state_conflict", "The application state has changed.")
        return {
            "application": _summary(
                services.review(actor, application.pk, approve=action == "approve")
            )
        }

    return _command("slack.applications.review", payload, context, actor, effect)


@transaction.atomic
def ban_preview(payload, *, context: ServiceContext):
    actor = _actor(context, ban=True)
    _payload(payload, {"member_id"})
    identifier = _uuid(payload.get("member_id"))
    services._lock()
    member = Membership.objects.filter(public_id=identifier).first()
    if member is None:
        raise SlackManagementError(404, "not_found", "The member was not found.")
    count, fingerprint = _ban_snapshot(member.pk)
    result = {"member_id": identifier, "affected_count": count, "scope_token": fingerprint}
    _audit("slack.members.preview", context, actor, metadata=result)
    return result


def ban_member(payload, *, context: ServiceContext):
    actor = _actor(context, ban=True)
    _payload(payload, {"member_id", "confirmed", "expected_count", "scope_token"})
    identifier = _uuid(payload.get("member_id"))
    expected = _integer(payload.get("expected_count"), maximum=200000)
    scope_token = payload.get("scope_token")
    if (
        not isinstance(scope_token, str)
        or len(scope_token) != 64
        or any(char not in "0123456789abcdef" for char in scope_token)
    ):
        raise SlackManagementError(400, "invalid_scope", "Preview the ban scope before confirming.")
    if payload.get("confirmed") is not True:
        raise SlackManagementError(400, "confirmation_required", "Confirm the displayed ban scope.")

    @transaction.atomic
    def effect():
        services._lock()
        member = Membership.objects.filter(public_id=identifier).first()
        if member is None:
            raise SlackManagementError(404, "not_found", "The member was not found.")
        count, fingerprint = _ban_snapshot(member.pk)
        if count != expected or not secrets.compare_digest(fingerprint, scope_token):
            raise SlackManagementError(
                409, "scope_conflict", "The ban scope changed; preview it again."
            )
        return {"member_id": identifier, "affected_count": services.ban(actor, member.pk)}

    return _command("slack.members.ban", payload, context, actor, effect)


def join_report(payload, *, context: ServiceContext):
    actor = _actor(context)
    _payload(payload, {"after", "limit", "classification"})
    after, limit = _page(payload)
    query = JoinObservation.objects.filter(pk__gt=after).exclude(classification="historical")
    classification = payload.get("classification")
    if classification is not None:
        if not isinstance(classification, str) or classification not in {
            "website_approved",
            "outside_flow",
            "unknown",
        }:
            raise SlackManagementError(
                400, "invalid_classification", "Choose a join classification."
            )
        query = query.filter(classification=classification)
    rows = list(query.select_related("application__sponsor").order_by("pk")[: limit + 1])
    monitor = MonitorState.objects.filter(key="slack-joins").first()
    _audit("slack.joins.report", context, actor, metadata={"row_count": min(len(rows), limit)})
    return {
        "joins": [
            {
                "id": row.pk,
                "slack_user_id": row.slack_user_id,
                "inviter_slack_id": row.inviter_slack_id,
                "classification": row.classification,
                "application_id": str(row.application.public_id) if row.application else None,
                "sponsor_id": (
                    str(row.application.sponsor.public_id)
                    if row.application and row.application.sponsor is not None
                    else None
                ),
                "observed_at": row.observed_at.isoformat(),
                "joined_at": row.joined_at.isoformat() if row.joined_at else None,
            }
            for row in rows[:limit]
        ],
        "next_after": rows[limit - 1].pk if len(rows) > limit else None,
        "monitor": {
            "baseline_at": monitor.baseline_at.isoformat()
            if monitor and monitor.baseline_at
            else None,
            "last_success_at": (
                monitor.last_success_at.isoformat() if monitor and monitor.last_success_at else None
            ),
        },
    }
