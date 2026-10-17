"""Launch capabilities are shared by every management adapter."""

from core.capabilities import (
    AdapterMetadata,
    Capability,
    ConcurrencyPolicy,
    IdempotencyPolicy,
    ServiceKind,
)
from courses.services.launch_commands import SEND_ACTIONS, WRITE_ACTIONS, send_launch, write_launch
from courses.services.launch_queries import read_launch


def can_send(actor, evidence):
    del evidence
    from accounts.studio_authorization import has_explicit_permission
    from management_auth.models import APIPrincipal
    from management_auth.services import principal_has_permission

    if isinstance(actor, APIPrincipal):
        return principal_has_permission(actor, "courses.manage_course_launch")
    return has_explicit_permission(actor, "courses.manage_course_launch")


def command_fields(suffix):
    actions = WRITE_ACTIONS
    if suffix == "send":
        actions = SEND_ACTIONS
    fields = {"action"}
    for _, allowed in actions.values():
        fields.update(allowed)
    return tuple(sorted(fields))


def api_metadata(key, suffix, method, path):
    fields = ()
    schema = None
    rate = "read"
    if method == "POST":
        fields = command_fields(suffix)
        schema = "LaunchCommand"
        rate = "write"
    return AdapterMetadata(
        route=f"/api/v1/admin/course-launch{path}",
        method=method,
        operation_id=key,
        scopes=(key,),
        request_schema=schema,
        result_schema="LaunchResult",
        writable_fields=fields,
        paginated=method == "GET",
        rate_class=rate,
    )


def capability_policies(suffix, method):
    policy = None
    kind = ServiceKind.QUERY
    idempotency = IdempotencyPolicy.NONE
    concurrency = ConcurrencyPolicy.NONE
    if suffix == "send":
        policy = can_send
    if method == "POST":
        kind = ServiceKind.COMMAND
        idempotency = IdempotencyPolicy.REQUIRED
        concurrency = ConcurrencyPolicy.REVISION
    return {
        "service_kind": kind,
        "idempotency": idempotency,
        "concurrency": concurrency,
        "function_policy": policy,
    }


def capability(suffix, service, permission, method, path):
    key = f"courses.launch.{suffix}"
    api = api_metadata(key, suffix, method, path)
    return Capability(
        key=key,
        description=f"{suffix.title()} course launch announcements",
        service=service,
        django_permission=f"courses.{permission}",
        studio=AdapterMetadata(
            route=f"studio:course-launch-{suffix}",
            method=method,
            operation_id=f"{key}.html",
            writable_fields=api.writable_fields,
        ),
        admin_api=api,
        audit_action=key,
        redacted_fields=(
            "body",
            "subject",
            "context",
            "token",
            "authorization",
            "destination",
            "preview_token",
        ),
        test_factory=lambda: {"items": []},
        **capability_policies(suffix, method),
        object_policy=lambda actor, target: True,
        object_scope=lambda actor, rows: rows,
        field_policy=lambda actor, field: field in api.writable_fields,
    )


LAUNCH_CAPABILITIES = (
    capability("read", read_launch, "read_course_launch", "GET", ""),
    capability("write", write_launch, "manage_course_launch", "POST", "/commands"),
    capability("send", send_launch, "send_course_launch", "POST", "/dispatch"),
)
