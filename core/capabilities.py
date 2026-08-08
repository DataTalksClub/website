"""Code-owned management capability declarations and fail-closed validation."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class ServiceKind(StrEnum):
    QUERY = "query"
    COMMAND = "command"


class IdempotencyPolicy(StrEnum):
    NONE = "none"
    OPTIONAL = "optional"
    REQUIRED = "required"


class ConcurrencyPolicy(StrEnum):
    NONE = "none"
    REVISION = "revision"
    IF_MATCH = "if_match"


PolicyHook = Callable[[Any, Any], bool]
ObjectScopeHook = Callable[[Any, Any], Any]
FieldPolicyHook = Callable[[Any, str], bool]
Service = Callable[..., Any]
TestFactory = Callable[[], Any]

_KEY = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_PERMISSION = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
_SAFE_METHODS = frozenset({"GET", "HEAD"})
_MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_ALL_METHODS = _SAFE_METHODS | _MUTATION_METHODS


@dataclass(frozen=True, slots=True)
class AdapterMetadata:
    """Transport metadata; test-only declarations are never runtime routes."""

    route: str
    method: str
    operation_id: str
    test_only: bool = False


@dataclass(frozen=True, slots=True)
class Capability:
    key: str
    description: str
    service_kind: ServiceKind
    service: Service
    django_permission: str
    studio: AdapterMetadata
    admin_api: AdapterMetadata
    idempotency: IdempotencyPolicy
    concurrency: ConcurrencyPolicy
    audit_action: str
    redacted_fields: tuple[str, ...]
    test_factory: TestFactory
    function_policy: PolicyHook | None = None
    object_policy: PolicyHook | None = None
    object_scope: ObjectScopeHook | None = None
    field_policy: FieldPolicyHook | None = None
    high_risk_policy: str | None = None
    test_only: bool = False


class CapabilityRegistryError(ValueError):
    """Raised when a code-owned capability declaration is unsafe or incomplete."""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = tuple(errors)
        super().__init__("; ".join(self.errors))


def _validate_adapter(
    name: str,
    adapter: object,
    errors: list[str],
    *,
    route_prefixes: tuple[str, ...],
) -> bool:
    if not isinstance(adapter, AdapterMetadata):
        errors.append(f"{name} metadata is missing or invalid")
        return False
    if (
        not isinstance(adapter.route, str)
        or not adapter.route.startswith(route_prefixes)
        or adapter.route in route_prefixes
    ):
        errors.append(f"{name} route is missing or invalid")
    if not isinstance(adapter.method, str) or adapter.method not in _ALL_METHODS:
        errors.append(f"{name} method is unsafe")
    if not isinstance(adapter.operation_id, str) or _KEY.fullmatch(adapter.operation_id) is None:
        errors.append(f"{name} operation ID is missing or invalid")
    if not isinstance(adapter.test_only, bool):
        errors.append(f"{name} test-only metadata is invalid")
    return True


def validate_capability(
    capability: Capability,
    *,
    resolved_high_risk_policies: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    errors: list[str] = []
    if not isinstance(capability, Capability):
        return ("capability declaration has an invalid type",)
    if not isinstance(capability.key, str) or _KEY.fullmatch(capability.key) is None:
        errors.append("capability key is missing or invalid")
    if not isinstance(capability.description, str) or not capability.description.strip():
        errors.append(f"{capability.key or 'capability'} description is missing")
    if not callable(capability.service):
        errors.append(f"{capability.key or 'capability'} service is missing")
    if (
        not isinstance(capability.django_permission, str)
        or _PERMISSION.fullmatch(capability.django_permission) is None
    ):
        errors.append(f"{capability.key or 'capability'} permission is missing or invalid")
    studio_valid = _validate_adapter(
        f"{capability.key or 'capability'} Studio",
        capability.studio,
        errors,
        route_prefixes=("studio:", "/studio/"),
    )
    api_valid = _validate_adapter(
        f"{capability.key or 'capability'} admin API",
        capability.admin_api,
        errors,
        route_prefixes=("/api/v1/admin/",),
    )
    methods = {
        adapter.method
        for adapter in (capability.studio, capability.admin_api)
        if studio_valid and api_valid and isinstance(adapter.method, str)
    }
    if not isinstance(capability.idempotency, IdempotencyPolicy):
        errors.append(f"{capability.key or 'capability'} idempotency metadata is invalid")
    if not isinstance(capability.concurrency, ConcurrencyPolicy):
        errors.append(f"{capability.key or 'capability'} concurrency metadata is invalid")
    if not isinstance(capability.service_kind, ServiceKind):
        errors.append(f"{capability.key or 'capability'} service kind is invalid")
    elif capability.service_kind is ServiceKind.QUERY:
        if not methods.issubset(_SAFE_METHODS):
            errors.append(f"{capability.key or 'capability'} query uses an unsafe method")
        if capability.idempotency is not IdempotencyPolicy.NONE:
            errors.append(f"{capability.key or 'capability'} query cannot require idempotency")
        if capability.concurrency is not ConcurrencyPolicy.NONE:
            errors.append(f"{capability.key or 'capability'} query cannot require concurrency")
    elif capability.service_kind is ServiceKind.COMMAND:
        if not methods.issubset(_MUTATION_METHODS):
            errors.append(f"{capability.key or 'capability'} command uses a safe method")
        if capability.idempotency is IdempotencyPolicy.NONE:
            errors.append(f"{capability.key or 'capability'} command lacks idempotency metadata")
    if (
        not isinstance(capability.audit_action, str)
        or _KEY.fullmatch(capability.audit_action) is None
    ):
        errors.append(f"{capability.key or 'capability'} audit metadata is missing")
    if (
        not isinstance(capability.redacted_fields, tuple)
        or not capability.redacted_fields
        or any(
            not isinstance(field, str) or not field.strip() for field in capability.redacted_fields
        )
    ):
        errors.append(f"{capability.key or 'capability'} redaction metadata is missing")
    elif len(set(capability.redacted_fields)) != len(capability.redacted_fields):
        errors.append(f"{capability.key or 'capability'} redaction metadata is duplicated")
    if not callable(capability.test_factory):
        errors.append(f"{capability.key or 'capability'} test factory is missing")
    for name, hook in (
        ("function", capability.function_policy),
        ("object", capability.object_policy),
        ("object scope", capability.object_scope),
        ("field", capability.field_policy),
    ):
        if hook is not None and not callable(hook):
            errors.append(f"{capability.key or 'capability'} {name} policy is invalid")
    if (capability.object_policy is None) != (capability.object_scope is None):
        errors.append(f"{capability.key or 'capability'} object policy lacks a query scope")
    if capability.high_risk_policy is not None:
        if (
            not isinstance(capability.high_risk_policy, str)
            or _KEY.fullmatch(capability.high_risk_policy) is None
        ):
            errors.append(f"{capability.key or 'capability'} high-risk policy is unresolved")
        elif (
            not capability.test_only
            and capability.high_risk_policy not in resolved_high_risk_policies
        ):
            errors.append(
                f"{capability.key or 'capability'} high-risk production policy is unresolved"
            )
    if not isinstance(capability.test_only, bool):
        errors.append(f"{capability.key or 'capability'} test-only declaration is invalid")
    elif (
        capability.test_only
        and studio_valid
        and api_valid
        and not (capability.studio.test_only and capability.admin_api.test_only)
    ):
        errors.append(f"{capability.key or 'capability'} test-only metadata contradicts")
    elif (
        not capability.test_only
        and studio_valid
        and api_valid
        and capability.studio.test_only
        and capability.admin_api.test_only
    ):
        errors.append(f"{capability.key or 'capability'} has no runtime adapter")
    return tuple(errors)


class CapabilityRegistry:
    """Immutable registry constructed only after every declaration validates."""

    def __init__(
        self,
        capabilities: Iterable[Capability],
        *,
        resolved_high_risk_policies: frozenset[str] = frozenset(),
    ) -> None:
        items = tuple(capabilities)
        errors: list[str] = []
        by_key: dict[str, Capability] = {}
        studio_routes: set[tuple[str, str]] = set()
        api_routes: set[tuple[str, str]] = set()
        operation_ids: set[str] = set()
        for item in items:
            errors.extend(
                validate_capability(
                    item,
                    resolved_high_risk_policies=resolved_high_risk_policies,
                )
            )
            if not isinstance(item, Capability):
                errors.append("capability declaration has an invalid type")
                continue
            if isinstance(item.key, str):
                if item.key in by_key:
                    errors.append(f"duplicate capability key: {item.key}")
                by_key[item.key] = item
            if not isinstance(item.studio, AdapterMetadata) or not isinstance(
                item.admin_api, AdapterMetadata
            ):
                continue
            if isinstance(item.studio.route, str) and isinstance(item.studio.method, str):
                studio_route = (item.studio.route, item.studio.method)
                if studio_route in studio_routes:
                    errors.append(
                        f"duplicate Studio route: {item.studio.route} {item.studio.method}"
                    )
                studio_routes.add(studio_route)
            if isinstance(item.admin_api.route, str) and isinstance(item.admin_api.method, str):
                api_route = (item.admin_api.route, item.admin_api.method)
                if api_route in api_routes:
                    errors.append(
                        f"duplicate admin API route: {item.admin_api.route} {item.admin_api.method}"
                    )
                api_routes.add(api_route)
            if isinstance(item.admin_api.operation_id, str):
                if item.admin_api.operation_id in operation_ids:
                    errors.append(f"duplicate admin API operation: {item.admin_api.operation_id}")
                operation_ids.add(item.admin_api.operation_id)
        if errors:
            raise CapabilityRegistryError(errors)
        self._items = items
        self._by_key = MappingProxyType(by_key)

    def __iter__(self) -> Iterator[Capability]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def get(self, key: str) -> Capability | None:
        return self._by_key.get(key)

    def require(self, key: str) -> Capability:
        capability = self.get(key)
        if capability is None:
            raise PermissionError("unknown capability")
        return capability
