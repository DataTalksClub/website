from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from accounts.studio_authorization import (
    StudioAuthenticationRequired,
    StudioAuthorizationDenied,
    authorize_studio_request,
)
from accounts.studio_sessions import session_reference
from core.high_risk import (
    DeterministicHighRiskPolicy,
    HighRiskEvidence,
    HighRiskRequest,
    execute_test_only_high_risk,
)
from core.models import Operation
from core.operations import finish_operation, start_operation
from core.services import ServiceContext
from management_api.operations import create_principal_operation
from management_auth.models import APICredential, APIPrincipal
from management_auth.services import (
    issue_credential_once,
    revoke_credential_once,
    rotate_credential_once,
)
from management_registry import CAPABILITY_REGISTRY


@dataclass(frozen=True, slots=True)
class BrowserActor:
    principal: APIPrincipal
    session_id: uuid.UUID
    authenticated_at: datetime


def _private(response: HttpResponse) -> HttpResponse:
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


def _browser_actor(request: HttpRequest, capability_key: str) -> BrowserActor | HttpResponse:
    capability = CAPABILITY_REGISTRY.require(capability_key)
    try:
        studio_principal = authorize_studio_request(
            request_user=request.user,
            session_reference=session_reference(request),
            capability=capability,
        )
    except StudioAuthenticationRequired:
        return _private(HttpResponse("Studio authentication required", status=401))
    except StudioAuthorizationDenied:
        return _private(HttpResponse("Studio access denied", status=403))
    principal = (
        APIPrincipal.objects.select_related("user")
        .filter(
            user=studio_principal.user,
            kind=APIPrincipal.Kind.HUMAN,
            is_active=True,
        )
        .first()
    )
    if principal is None:
        return _private(HttpResponse("Studio access denied", status=403))
    return BrowserActor(
        principal=principal,
        session_id=studio_principal.session.session_id,
        authenticated_at=studio_principal.session.authenticated_at,
    )


def _authorize_action(
    actor: BrowserActor,
    *,
    capability_key: str,
    target_revision: int,
    impact: str,
    idempotency_key: str,
) -> None:
    capability = CAPABILITY_REGISTRY.require(capability_key)
    evidence = HighRiskEvidence(
        capability_key=capability.key,
        actor_id=actor.principal.user_id,
        session_id=actor.session_id,
        authenticated_at=actor.authenticated_at,
        target_revision=target_revision,
        scope="management.credentials",
        expected_count=1,
        impact=impact,
        confirmed=True,
    )
    request = HighRiskRequest(
        evidence=evidence,
        preview_evidence=evidence,
        idempotency_key=f"{actor.principal.id}:{idempotency_key}",
    )
    execute_test_only_high_risk(
        capability=capability,
        request=request,
        policy=DeterministicHighRiskPolicy(
            expected=evidence,
            fixture_not_before=evidence.authenticated_at - timedelta(seconds=1),
        ),
        context=ServiceContext.from_current(actor_ref=f"user:{actor.principal.user_id}"),
    )


def _scoped_target(actor: BrowserActor, raw_id: object) -> APIPrincipal | None:
    capability = CAPABILITY_REGISTRY.require("management.credentials.create.fixture")
    scope = capability.object_scope
    policy = capability.object_policy
    if scope is None or policy is None:
        return None
    try:
        target = scope(actor.principal, APIPrincipal.objects.all()).filter(pk=raw_id).first()
        return target if target is not None and policy(actor.principal, target) is True else None
    except Exception:
        return None


def _scoped_credential(actor: BrowserActor, raw_id: object) -> APICredential | None:
    capability = CAPABILITY_REGISTRY.require("management.credentials.rotate.fixture")
    scope = capability.object_scope
    policy = capability.object_policy
    if scope is None or policy is None:
        return None
    try:
        credential = (
            scope(actor.principal, APICredential.objects.select_related("principal"))
            .filter(pk=raw_id)
            .first()
        )
        return (
            credential
            if credential is not None and policy(actor.principal, credential) is True
            else None
        )
    except Exception:
        return None


def _start_browser_operation(actor: BrowserActor, action: str) -> Operation:
    operation = create_principal_operation(
        principal=actor.principal,
        kind=f"fixture.credential.{action}",
        cancellable=False,
        progress_total=1,
    )
    return start_operation(operation_id=operation.id, expected_revision=operation.revision)


def _finish_browser_operation(operation: Operation, *, succeeded: bool) -> Operation:
    return finish_operation(
        operation_id=operation.id,
        expected_revision=operation.revision,
        succeeded=succeeded,
        result_summary={"credential_action": operation.kind.rsplit(".", 1)[-1]},
        errors=[] if succeeded else [{"code": "fixture_action_failed"}],
    )


def credential_lifecycle(request: HttpRequest) -> HttpResponse:
    selected = _browser_actor(request, "management.credentials.create.fixture")
    if isinstance(selected, HttpResponse):
        return selected
    actor = selected
    raw_token = ""
    notice = ""
    error_message = ""

    if request.method == "POST":
        action = request.POST.get("action", "")
        idempotency_key = request.POST.get("idempotency_key", "")
        if action not in {"create", "rotate", "revoke"} or not idempotency_key:
            return _private(HttpResponse("Invalid credential fixture request", status=400))
        try:
            if action == "create":
                target = _scoped_target(actor, request.POST.get("target_principal_id"))
                if target is None:
                    raise PermissionError
                _authorize_action(
                    actor,
                    capability_key="management.credentials.create.fixture",
                    target_revision=target.revision,
                    impact="browser-credential-create",
                    idempotency_key=idempotency_key,
                )
                result = issue_credential_once(
                    actor_principal=actor.principal,
                    target_principal_id=target.id,
                    name="Browser fixture credential",
                    scopes=("studio.home.read",),
                    idempotency_key=idempotency_key,
                    actor_permission="core.execute_high_risk_fixture",
                    created_by=actor.principal.user,
                )
                raw_token = str(result.response["token"])
                notice = "Credential created"
            else:
                credential = _scoped_credential(actor, request.POST.get("credential_id"))
                if credential is None:
                    raise PermissionError
                expected_revision = int(request.POST.get("expected_revision", "0"))
                capability_key = f"management.credentials.{action}.fixture"
                _authorize_action(
                    actor,
                    capability_key=capability_key,
                    target_revision=expected_revision,
                    impact=f"browser-credential-{action}",
                    idempotency_key=idempotency_key,
                )
                if action == "rotate":
                    result = rotate_credential_once(
                        actor_principal=actor.principal,
                        credential_id=credential.id,
                        expected_revision=expected_revision,
                        idempotency_key=idempotency_key,
                        actor_permission="core.execute_high_risk_fixture",
                        created_by=actor.principal.user,
                    )
                    raw_token = str(result.response["token"])
                    notice = "Credential rotated"
                else:
                    revoke_credential_once(
                        actor_principal=actor.principal,
                        credential_id=credential.id,
                        expected_revision=expected_revision,
                        idempotency_key=idempotency_key,
                        actor_permission="core.execute_high_risk_fixture",
                    )
                    notice = "Credential revoked"
            operation = _start_browser_operation(actor, action)
            _finish_browser_operation(operation, succeeded=True)
        except Exception:
            error_message = "The credential action could not be completed."

    targets = APIPrincipal.objects.filter(
        kind=APIPrincipal.Kind.SERVICE,
        is_active=True,
    ).order_by("name", "id")
    credentials = APICredential.objects.filter(principal__in=targets).order_by("-created_at", "-id")
    operations = Operation.objects.filter(api_principal=actor.principal).order_by(
        "-created_at", "-id"
    )[:10]
    response = render(
        request,
        "management_api/credential_fixture.html",
        {
            "targets": targets,
            "credentials": credentials,
            "operations": operations,
            "raw_token": raw_token,
            "notice": notice,
            "error_message": error_message,
            "idempotency_key": uuid.uuid4(),
        },
    )
    return _private(response)


def credential_away(request: HttpRequest) -> HttpResponse:
    selected = _browser_actor(request, "management.credentials.create.fixture")
    if isinstance(selected, HttpResponse):
        return selected
    return _private(
        render(
            request,
            "management_api/credential_fixture_away.html",
        )
    )
