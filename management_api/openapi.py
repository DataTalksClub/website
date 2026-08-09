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
            "version": settings.VERSION,
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
                    "required": ["status", "version", "source_sha", "image_digest"],
                    "properties": {
                        "status": {"type": "string", "const": "ok"},
                        "version": {"type": "string"},
                        "source_sha": {"type": ["string", "null"]},
                        "image_digest": {"type": ["string", "null"]},
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
                "APIError": _error_schema(),
            },
        },
    }


def render_document() -> str:
    return json.dumps(generate_document(), indent=2, sort_keys=True) + "\n"
