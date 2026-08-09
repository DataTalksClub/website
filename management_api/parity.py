from __future__ import annotations

import re
from dataclasses import dataclass

from core.capabilities import ServiceKind
from core.management_health import read_management_health
from management_registry import CAPABILITY_REGISTRY


@dataclass(frozen=True, slots=True)
class RuntimeOperation:
    route: str
    method: str
    capability_key: str
    operation_id: str
    service: object
    result_schema: str
    audit_action: str


def _document_route(route: str) -> str:
    return re.sub(r"<(?:[a-z_][a-z0-9_]*:)?([a-z_][a-z0-9_]*)>", r"{\1}", route)


def runtime_operations() -> tuple[RuntimeOperation, ...]:
    from website.admin_api_urls import urlpatterns

    operations: list[RuntimeOperation] = []
    for pattern in urlpatterns:
        callback = pattern.callback
        if bool(getattr(callback, "management_api_exempt", False)):
            continue
        route = getattr(pattern.pattern, "_route", None)
        if not isinstance(route, str):
            continue
        capability_keys = getattr(callback, "management_capability_keys", None)
        if capability_keys is None:
            capability_keys = (getattr(callback, "management_capability_key", ""),)
        for capability_key in capability_keys:
            capability = CAPABILITY_REGISTRY.get(capability_key)
            method = capability.admin_api.method if capability is not None else ""
            method_views = getattr(callback, "management_capability_views", {})
            bound_view = method_views.get(method, callback)
            operation_id = (
                getattr(bound_view, "management_operation_id", "") if capability is not None else ""
            )
            operations.append(
                RuntimeOperation(
                    route=f"/api/v1/admin/{_document_route(route)}",
                    method=method,
                    capability_key=capability_key,
                    operation_id=operation_id,
                    service=getattr(bound_view, "management_service", None),
                    result_schema=getattr(bound_view, "management_result_schema", ""),
                    audit_action=getattr(bound_view, "management_audit_action", ""),
                )
            )
    return tuple(operations)


def parity_errors() -> tuple[str, ...]:
    errors: list[str] = []
    runtime = runtime_operations()
    runtime_by_route = {(item.route, item.method): item for item in runtime}
    declarations = {
        (capability.admin_api.route, capability.admin_api.method): capability
        for capability in CAPABILITY_REGISTRY
        if not capability.admin_api.test_only
    }
    for route_method, capability in declarations.items():
        operation = runtime_by_route.get(route_method)
        if operation is None:
            errors.append(f"admin route is missing for {capability.key}")
            continue
        if operation.capability_key != capability.key:
            errors.append(f"admin route capability drifted for {capability.key}")
        if operation.operation_id != capability.admin_api.operation_id:
            errors.append(f"admin operation ID drifted for {capability.key}")
        if operation.service is not capability.service:
            errors.append(f"admin service drifted for {capability.key}")
        if operation.result_schema != capability.admin_api.result_schema:
            errors.append(f"admin result schema drifted for {capability.key}")
        if operation.audit_action != capability.audit_action:
            errors.append(f"admin audit action drifted for {capability.key}")
        if (
            capability.service_kind is ServiceKind.QUERY
            and capability.service is read_management_health
        ):
            expected = capability.test_factory()
            actual = capability.service(None, context=None)
            if expected != actual:
                errors.append(f"admin service result drifted for {capability.key}")
    for route_method, operation in runtime_by_route.items():
        if route_method not in declarations:
            errors.append(f"runtime admin route lacks a capability: {operation.route}")
        if "_fixtures" in operation.route:
            errors.append(f"test fixture is mounted at runtime: {operation.route}")
    return tuple(errors)
