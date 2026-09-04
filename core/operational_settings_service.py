"""The admin API's read and write surface for the operational tunables.

``core.operational_settings`` declares the values; ``core.runtime_config``
resolves them; this module is what an operator actually reaches.  It is the same
compare-and-swap batch writer the public announcement keys use
(``core.settings_batch``), pointed at the operational key tuple, so there is one
implementation of "normalize the batch, refuse it if a revision moved, write the
rows, record the trail".

The read is deliberately richer than the public one.  A stored row is only the
first of four layers, so listing the row alone would tell an operator almost
nothing: they need to see the value the running processes are actually using and
which layer produced it, because that is what tells them whether writing a row
would change anything.  Both extra fields go through the same registry
validation as the stored value, which refuses secret-bearing content outright.
"""

from __future__ import annotations

import uuid
from typing import Any

from core.capabilities import (
    AdapterMetadata,
    Capability,
    ConcurrencyPolicy,
    IdempotencyPolicy,
    ServiceKind,
)
from core.configuration import InvalidOperationalSetting
from core.idempotency import JsonObject, JsonValue
from core.operational_settings import OPERATIONAL_SETTING_KEYS
from core.runtime_config import resolve_runtime_setting
from core.services import ServiceContext
from core.settings_batch import (
    SettingsBatchResult,
    SettingsScope,
    query_settings,
    scope_definitions,
    scope_settings_factory,
    update_settings,
)

OPERATIONAL_SETTINGS_READ_PERMISSION = "core.read_operational_settings"
OPERATIONAL_SETTINGS_WRITE_PERMISSION = "core.change_operational_settings"
OPERATIONAL_SETTINGS_AUDIT_READ = "core.runtime_settings.read"
OPERATIONAL_SETTINGS_AUDIT_WRITE = "core.runtime_settings.batch_updated"

OPERATIONAL_SETTINGS_SCOPE = SettingsScope(
    keys=OPERATIONAL_SETTING_KEYS,
    sensitivity="operational",
    audit_label="operational.runtime",
    audit_action_write=OPERATIONAL_SETTINGS_AUDIT_WRITE,
    idempotency_prefix="operational.settings.write",
)


def _with_runtime_resolution(item: JsonValue, *, using: str) -> JsonObject:
    """Add the effective value and the layer that produced it to one row."""

    if not isinstance(item, dict):
        raise InvalidOperationalSetting("operational setting query result is invalid")
    key = item.get("key")
    if not isinstance(key, str):
        raise InvalidOperationalSetting("operational setting query result is invalid")
    resolution = resolve_runtime_setting(key, using=using)
    return {**item, "effective_value": resolution.value, "effective_layer": resolution.layer}


def query_operational_settings(
    _query: object = None,
    *,
    context: ServiceContext | None = None,
    using: str = "default",
) -> JsonObject:
    """List every operational tunable, its stored row, and its effective value."""

    del context
    definitions = {
        definition.key: definition for definition in scope_definitions(OPERATIONAL_SETTINGS_SCOPE)
    }
    stored = query_settings(OPERATIONAL_SETTINGS_SCOPE, using=using).get("settings")
    if not isinstance(stored, list):
        raise InvalidOperationalSetting("operational setting query result is invalid")
    settings: list[JsonValue] = []
    for item in stored:
        resolved = _with_runtime_resolution(item, using=using)
        definition = definitions[str(resolved["key"])]
        # The registry refuses a secret-bearing environment or settings name, so
        # naming them here tells an operator where the value comes from today
        # without exposing anything the registry would not already store.
        resolved["env_var"] = definition.env_var
        resolved["settings_attr"] = definition.settings_attr
        settings.append(resolved)
    return {"settings": settings}


def update_operational_settings(
    *,
    updates: object,
    source: str,
    idempotency_key: str,
    actor_ref: str,
    actor_id: Any | None = None,
    api_principal_id: uuid.UUID | None = None,
    context: ServiceContext | None = None,
    using: str = "default",
) -> SettingsBatchResult:
    """Apply one actor-scoped idempotent batch of operational settings."""

    return update_settings(
        OPERATIONAL_SETTINGS_SCOPE,
        updates=updates,
        source=source,
        idempotency_key=idempotency_key,
        actor_ref=actor_ref,
        actor_id=actor_id,
        api_principal_id=api_principal_id,
        context=context,
        using=using,
    )


def _factory() -> JsonObject:
    return scope_settings_factory(OPERATIONAL_SETTINGS_SCOPE)


def _field_policy(_actor: object, field: str) -> bool:
    return field == "updates"


_READ_REDACTED_FIELDS = (
    "authorization",
    "cookie",
    "default",
    "effective_value",
    "token",
    "value",
)

OPERATIONAL_SETTINGS_READ = Capability(
    key="settings.operational.read",
    description="Read operator-tunable runtime settings and their effective values",
    service_kind=ServiceKind.QUERY,
    service=query_operational_settings,
    django_permission=OPERATIONAL_SETTINGS_READ_PERMISSION,
    studio=AdapterMetadata(
        # There is no Studio page for these yet; the admin API is the runtime
        # adapter.  Declaring the Studio side as test-only is how the registry
        # records "not mounted" without pointing the Studio navigation at a
        # route that does not exist.
        route="/studio/settings/operational",
        method="GET",
        operation_id="settings.operational.read.html",
        test_only=True,
    ),
    admin_api=AdapterMetadata(
        route="/api/v1/admin/settings/operational",
        method="GET",
        operation_id="settings.operational.read",
        scopes=("settings.operational.read",),
        result_schema="OperationalSettings",
        rate_class="read",
        rate_cost=1,
    ),
    idempotency=IdempotencyPolicy.NONE,
    concurrency=ConcurrencyPolicy.NONE,
    audit_action=OPERATIONAL_SETTINGS_AUDIT_READ,
    redacted_fields=_READ_REDACTED_FIELDS,
    test_factory=_factory,
)

OPERATIONAL_SETTINGS_WRITE = Capability(
    key="settings.operational.write",
    description="Update operator-tunable runtime settings without a restart",
    service_kind=ServiceKind.COMMAND,
    service=update_operational_settings,
    django_permission=OPERATIONAL_SETTINGS_WRITE_PERMISSION,
    studio=AdapterMetadata(
        route="/studio/settings/operational",
        method="PATCH",
        operation_id="settings.operational.write.html",
        writable_fields=("updates",),
        test_only=True,
    ),
    admin_api=AdapterMetadata(
        route="/api/v1/admin/settings/operational",
        method="PATCH",
        operation_id="settings.operational.write",
        scopes=("settings.operational.write",),
        request_schema="OperationalSettingsBatchRequest",
        result_schema="OperationalSettingsBatchResult",
        writable_fields=("updates",),
        rate_class="write",
        rate_cost=1,
    ),
    idempotency=IdempotencyPolicy.REQUIRED,
    concurrency=ConcurrencyPolicy.REVISION,
    audit_action=OPERATIONAL_SETTINGS_AUDIT_WRITE,
    redacted_fields=(
        "authorization",
        "body",
        "cookie",
        "csrfmiddlewaretoken",
        "default",
        "effective_value",
        "token",
        "updates",
        "value",
    ),
    test_factory=_factory,
    field_policy=_field_policy,
)

OPERATIONAL_SETTING_CAPABILITIES = (OPERATIONAL_SETTINGS_READ, OPERATIONAL_SETTINGS_WRITE)
