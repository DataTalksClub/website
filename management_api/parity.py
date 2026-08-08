from __future__ import annotations

from dataclasses import dataclass

from management_registry import CAPABILITY_REGISTRY


@dataclass(frozen=True, slots=True)
class RuntimeOperation:
    route: str
    method: str
    capability_key: str
    operation_id: str


def runtime_operations() -> tuple[RuntimeOperation, ...]:
    from website.admin_api_urls import urlpatterns

    operations: list[RuntimeOperation] = []
    for pattern in urlpatterns:
        callback = pattern.callback
        if bool(getattr(callback, "management_api_exempt", False)):
            continue
        capability_key = getattr(callback, "management_capability_key", "")
        operation_id = getattr(callback, "management_operation_id", "")
        route = getattr(pattern.pattern, "_route", None)
        if not isinstance(route, str):
            continue
        capability = CAPABILITY_REGISTRY.get(capability_key)
        method = capability.admin_api.method if capability is not None else ""
        operations.append(
            RuntimeOperation(
                route=f"/api/v1/admin/{route}",
                method=method,
                capability_key=capability_key,
                operation_id=operation_id,
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
