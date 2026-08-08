from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.conf import settings

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
            }
        },
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
                "APIError": _error_schema(),
            },
        },
    }


def render_document() -> str:
    return json.dumps(generate_document(), indent=2, sort_keys=True) + "\n"
