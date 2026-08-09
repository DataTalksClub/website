from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.conf import settings

from core.capabilities import ConcurrencyPolicy, IdempotencyPolicy
from management_registry import CAPABILITY_REGISTRY

SCHEMA_PATH = Path(settings.BASE_DIR) / "_docs/api/admin-openapi.json"
ADMIN_API_PREFIX = "/api/v1/admin"


def document_path(runtime_route: str) -> str:
    """Return the path relative to the document's declared admin API server."""

    if not runtime_route.startswith(f"{ADMIN_API_PREFIX}/"):
        raise ValueError("management route is outside the admin API server")
    return runtime_route.removeprefix(ADMIN_API_PREFIX)


def _error_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["error"],
        "properties": {
            "error": {
                "type": "object",
                "additionalProperties": False,
                "required": ["code", "message", "request_id"],
                "properties": {
                    "code": {"type": "string"},
                    "message": {"type": "string"},
                    "request_id": {"type": "string"},
                    "fields": {
                        "type": "object",
                        "additionalProperties": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                },
            },
            "result": {"$ref": "#/components/schemas/CredentialMetadata"},
        },
    }


def _credential_schema(*, include_token: bool = False) -> dict[str, Any]:
    required = [
        "credential_id",
        "name",
        "principal_id",
        "principal_label",
        "prefix",
        "scopes",
        "expires_at",
        "state",
        "last_used_at",
        "created_at",
        "revision",
    ]
    properties: dict[str, Any] = {
        "credential_id": {"type": "string", "format": "uuid"},
        "name": {"type": "string", "maxLength": 120},
        "principal_id": {"type": "string", "format": "uuid"},
        "principal_label": {"type": "string", "maxLength": 120},
        "prefix": {"type": "string", "minLength": 16, "maxLength": 16},
        "scopes": {
            "type": "array",
            "minItems": 1,
            "maxItems": 64,
            "items": {"type": "string"},
        },
        "expires_at": {"type": "string", "format": "date-time"},
        "state": {
            "type": "string",
            "enum": ["active", "expired", "revoked", "rotated"],
        },
        "last_used_at": {"type": ["string", "null"], "format": "date-time"},
        "created_at": {"type": "string", "format": "date-time"},
        "revision": {"type": "integer", "minimum": 1},
    }
    if include_token:
        required.append("token")
        properties["token"] = {
            "type": "string",
            "description": "One-time response value; never recoverable.",
        }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


def generate_document() -> dict[str, Any]:
    paths: dict[str, Any] = {}
    for capability in CAPABILITY_REGISTRY:
        adapter = capability.admin_api
        if adapter.test_only:
            continue
        operation: dict[str, Any] = {
            "operationId": adapter.operation_id,
            "summary": capability.description,
            "security": [{"BearerAuth": list(adapter.scopes)}],
            "x-capability-key": capability.key,
            "x-django-permission": capability.django_permission,
            "x-audit-action": capability.audit_action,
            "x-concurrency": capability.concurrency.value,
            "x-idempotency": capability.idempotency.value,
            "x-rate-class": adapter.rate_class,
            "x-rate-cost": adapter.rate_cost,
            "responses": {
                str(adapter.success_status): {
                    "description": "Success",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": f"#/components/schemas/{adapter.result_schema}"}
                        }
                    },
                },
                **{
                    str(status): {
                        "description": "Safe management API error",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/APIError"}
                            }
                        },
                    }
                    for status in (400, 401, 403, 404, 405, 409, 413, 415, 428, 429, 500)
                },
            },
        }
        parameters: list[dict[str, Any]] = []
        if capability.idempotency is IdempotencyPolicy.REQUIRED:
            parameters.append(
                {
                    "name": "Idempotency-Key",
                    "in": "header",
                    "required": True,
                    "schema": {"type": "string", "minLength": 1, "maxLength": 512},
                }
            )
        if capability.concurrency is ConcurrencyPolicy.IF_MATCH:
            parameters.append(
                {
                    "name": "If-Match",
                    "in": "header",
                    "required": True,
                    "schema": {"type": "string", "pattern": '^"rev-[1-9][0-9]*"$'},
                }
            )
        if parameters:
            operation["parameters"] = parameters
        if adapter.request_schema:
            operation["requestBody"] = {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {"$ref": f"#/components/schemas/{adapter.request_schema}"}
                    }
                },
            }
        paths.setdefault(document_path(adapter.route), {})[adapter.method.casefold()] = operation
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "DataTalks.Club management API",
            "version": "1.0.0",
        },
        "servers": [{"url": "/api/v1/admin"}],
        "paths": paths,
        "components": {
            "securitySchemes": {
                "BearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "Strict dtca_v1 management credential",
                }
            },
            "schemas": {
                "AdminHealth": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["status", "version"],
                    "properties": {
                        "status": {"type": "string", "const": "ok"},
                        "version": {"type": "string"},
                    },
                },
                "CredentialMetadata": _credential_schema(),
                "CredentialSecret": _credential_schema(include_token=True),
                "CredentialList": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["items", "page", "page_size", "total_count"],
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/CredentialMetadata"},
                        },
                        "page": {"type": "integer", "minimum": 1},
                        "page_size": {"type": "integer", "minimum": 1, "maximum": 100},
                        "total_count": {"type": "integer", "minimum": 0},
                    },
                },
                "CredentialCreateRequest": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "target_principal_id",
                        "name",
                        "scopes",
                        "confirmed",
                    ],
                    "properties": {
                        "target_principal_id": {"type": "string", "format": "uuid"},
                        "name": {"type": "string", "minLength": 1, "maxLength": 120},
                        "scopes": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 64,
                            "uniqueItems": True,
                            "items": {"type": "string"},
                        },
                        "expires_at": {"type": "string", "format": "date-time"},
                        "confirmed": {"type": "boolean", "const": True},
                    },
                },
                "CredentialRotateRequest": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["confirmed"],
                    "properties": {
                        "expires_at": {"type": "string", "format": "date-time"},
                        "overlap_seconds": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 3600,
                            "default": 0,
                        },
                        "confirmed": {"type": "boolean", "const": True},
                    },
                },
                "CredentialRevokeRequest": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["confirmed"],
                    "properties": {
                        "confirmed": {"type": "boolean", "const": True},
                    },
                },
                "HistoricalRegistrationImport": {
                    "type": "object",
                    "additionalProperties": True,
                    "required": [
                        "id",
                        "provider",
                        "source_checksum",
                        "state",
                        "revision",
                    ],
                    "properties": {
                        "id": {"type": "string", "format": "uuid"},
                        "provider": {"type": "string", "enum": ["luma", "eventbrite"]},
                        "source_checksum": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                        "state": {
                            "type": "string",
                            "enum": [
                                "staged",
                                "validated",
                                "active",
                                "cancelled",
                                "rolled_back",
                                "quarantined",
                            ],
                        },
                        "revision": {"type": "integer", "minimum": 1},
                    },
                },
                "HistoricalRegistrationImportDetail": {
                    "allOf": [
                        {"$ref": "#/components/schemas/HistoricalRegistrationImport"},
                        {
                            "type": "object",
                            "required": ["aggregates"],
                            "properties": {
                                "aggregates": {
                                    "type": "array",
                                    "items": {"type": "object", "additionalProperties": True},
                                }
                            },
                        },
                    ]
                },
                "HistoricalRegistrationImportList": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["items", "page", "page_size", "total_count"],
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/HistoricalRegistrationImport"},
                        },
                        "page": {"type": "integer", "minimum": 1},
                        "page_size": {"type": "integer", "minimum": 1, "maximum": 100},
                        "total_count": {"type": "integer", "minimum": 0},
                    },
                },
                "HistoricalRegistrationImportCreateRequest": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["provider", "source_reference", "mapping_set_revision"],
                    "properties": {
                        "provider": {"type": "string", "enum": ["luma", "eventbrite"]},
                        "source_reference": {
                            "type": "string",
                            "pattern": "^[a-z][a-z0-9_.:-]{0,127}$",
                        },
                        "mapping_set_revision": {"type": "integer", "minimum": 1},
                    },
                },
                "HistoricalRegistrationImportActionRequest": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["confirmed", "reason_code"],
                    "properties": {
                        "confirmed": {"type": "boolean", "const": True},
                        "reason_code": {"type": "string", "pattern": "^[a-z][a-z0-9_]{0,63}$"},
                    },
                },
                "HistoricalRegistrationImportActionResult": {
                    "type": "object",
                    "additionalProperties": True,
                    "required": ["run_id", "state", "replayed"],
                    "properties": {
                        "run_id": {"type": "string", "format": "uuid"},
                        "state": {"type": "string"},
                        "replayed": {"type": "boolean"},
                    },
                },
                "HistoricalEventMapping": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "id",
                        "provider",
                        "external_event_identifier",
                        "state",
                        "mapping_set_revision",
                        "revision",
                    ],
                    "properties": {
                        "id": {"type": "string", "format": "uuid"},
                        "provider": {"type": "string", "enum": ["luma", "eventbrite"]},
                        "external_event_identifier": {"type": "string"},
                        "canonical_slug": {"type": "string"},
                        "state": {
                            "type": "string",
                            "enum": ["review_required", "mapped", "excluded", "source_missing"],
                        },
                        "mapping_set_revision": {"type": "integer", "minimum": 1},
                        "revision": {"type": "integer", "minimum": 1},
                        "reason_code": {"type": "string"},
                        "created_at": {"type": "string", "format": "date-time"},
                        "updated_at": {"type": "string", "format": "date-time"},
                    },
                },
                "HistoricalEventMappingList": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["items", "page", "page_size", "total_count"],
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/HistoricalEventMapping"},
                        },
                        "page": {"type": "integer", "minimum": 1},
                        "page_size": {"type": "integer", "minimum": 1, "maximum": 100},
                        "total_count": {"type": "integer", "minimum": 0},
                    },
                },
                "HistoricalEventMappingCreateRequest": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "provider",
                        "external_event_identifier",
                        "state",
                        "canonical_slug",
                        "mapping_set_revision",
                        "reason_code",
                        "reason",
                        "coverage_boundary",
                        "combination_policy",
                    ],
                    "properties": {
                        "provider": {"type": "string", "enum": ["luma", "eventbrite"]},
                        "external_event_identifier": {"type": "string", "maxLength": 512},
                        "state": {"type": "string", "enum": ["mapped", "excluded"]},
                        "canonical_slug": {"type": "string"},
                        "mapping_set_revision": {"type": "integer", "minimum": 1},
                        "reason_code": {"type": "string"},
                        "reason": {"type": "string", "maxLength": 2000},
                        "coverage_boundary": {"type": "string"},
                        "combination_policy": {
                            "type": "string",
                            "enum": ["additive_disjoint", "replacement", "exclude"],
                        },
                    },
                },
                "HistoricalEventMappingUpdateRequest": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "state",
                        "canonical_slug",
                        "mapping_set_revision",
                        "reason_code",
                        "reason",
                        "coverage_boundary",
                        "combination_policy",
                    ],
                    "properties": {
                        "state": {"type": "string", "enum": ["mapped", "excluded"]},
                        "canonical_slug": {"type": "string"},
                        "mapping_set_revision": {"type": "integer", "minimum": 1},
                        "reason_code": {"type": "string"},
                        "reason": {"type": "string", "maxLength": 2000},
                        "coverage_boundary": {"type": "string"},
                        "combination_policy": {
                            "type": "string",
                            "enum": ["additive_disjoint", "replacement", "exclude"],
                        },
                    },
                },
                "HistoricalRegistrationTotal": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "canonical_slug",
                        "complete",
                        "count",
                        "total_revision",
                        "contributions",
                    ],
                    "properties": {
                        "canonical_slug": {"type": "string"},
                        "complete": {"type": "boolean"},
                        "count": {"type": ["integer", "null"], "minimum": 0},
                        "total_revision": {"type": ["integer", "null"], "minimum": 1},
                        "contributions": {
                            "type": "array",
                            "items": {"type": "object", "additionalProperties": True},
                        },
                    },
                },
                "APIError": _error_schema(),
            },
        },
    }


def render_document() -> str:
    return json.dumps(generate_document(), indent=2, sort_keys=True) + "\n"
