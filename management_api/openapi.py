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
            "result": {
                "oneOf": [
                    {"$ref": "#/components/schemas/CredentialMetadata"},
                    {"$ref": "#/components/schemas/SiteSettingRevisionConflict"},
                    {"$ref": "#/components/schemas/SiteNavigationRevisionConflict"},
                    {"$ref": "#/components/schemas/SponsorRevisionConflict"},
                ]
            },
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


def _site_setting_schema(*, include_changed: bool = False) -> dict[str, Any]:
    required = [
        "key",
        "group",
        "label",
        "description",
        "value_type",
        "default",
        "validation",
        "docs_reference",
        "lifecycle",
        "cache_policy",
        "sensitivity",
        "value",
        "source",
        "definition_version",
        "revision",
    ]
    properties: dict[str, Any] = {
        "key": {
            "type": "string",
            "enum": ["site.announcement.enabled", "site.announcement.message"],
        },
        "group": {"type": "string", "const": "site.announcement"},
        "label": {"type": "string", "minLength": 1},
        "description": {"type": "string", "minLength": 1},
        "value_type": {"type": "string", "enum": ["boolean", "string"]},
        "default": {"type": ["boolean", "string"]},
        "validation": {"type": "object"},
        "docs_reference": {"type": "string", "pattern": "^_docs/"},
        "lifecycle": {"type": "string", "const": "active"},
        "cache_policy": {"type": "string", "const": "uncached"},
        "sensitivity": {"type": "string", "const": "public"},
        "value": {"type": ["boolean", "string"]},
        "source": {"type": "string", "enum": ["code_default", "studio", "admin_api"]},
        "definition_version": {"type": "integer", "minimum": 1},
        "revision": {"type": "integer", "minimum": 0},
    }
    if include_changed:
        required.append("changed")
        properties["changed"] = {"type": "boolean"}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
        "oneOf": [
            {
                "properties": {
                    "key": {"const": "site.announcement.enabled"},
                    "value_type": {"const": "boolean"},
                    "default": {"type": "boolean"},
                    "value": {"type": "boolean"},
                    "validation": {
                        "type": "object",
                        "maxProperties": 0,
                    },
                }
            },
            {
                "properties": {
                    "key": {"const": "site.announcement.message"},
                    "value_type": {"const": "string"},
                    "default": {"type": "string", "maxLength": 500},
                    "value": {"type": "string", "maxLength": 500},
                    "validation": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["max_length", "single_line", "trim", "markup"],
                        "properties": {
                            "max_length": {"const": 500},
                            "single_line": {"const": True},
                            "trim": {"const": True},
                            "markup": {"const": False},
                        },
                    },
                }
            },
        ],
    }


def _site_setting_update_schemas() -> list[dict[str, Any]]:
    common = {
        "expected_revision": {"type": "integer", "minimum": 0},
    }
    return [
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["key", "value", "expected_revision"],
            "properties": {
                "key": {"type": "string", "const": "site.announcement.enabled"},
                "value": {"type": "boolean"},
                **common,
            },
        },
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["key", "value", "expected_revision"],
            "properties": {
                "key": {"type": "string", "const": "site.announcement.message"},
                "value": {
                    "type": "string",
                    "maxLength": 500,
                    "description": (
                        "One public-safe line; the server trims Unicode whitespace and rejects "
                        "markup and control characters."
                    ),
                },
                **common,
            },
        },
    ]


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
            if_match_pattern = (
                '^"rev-[0-9]+"$'
                if capability.key == "site.navigation.write"
                else '^"rev-[1-9][0-9]*"$'
            )
            parameters.append(
                {
                    "name": "If-Match",
                    "in": "header",
                    "required": True,
                    "schema": {"type": "string", "pattern": if_match_pattern},
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
                "SiteSetting": _site_setting_schema(),
                "SiteSettingResult": _site_setting_schema(include_changed=True),
                "SiteSettings": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["settings"],
                    "properties": {
                        "settings": {
                            "type": "array",
                            "minItems": 2,
                            "maxItems": 2,
                            "items": {"$ref": "#/components/schemas/SiteSetting"},
                        }
                    },
                },
                "SiteSettingsBatchRequest": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["updates"],
                    "properties": {
                        "updates": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 2,
                            "items": {"oneOf": _site_setting_update_schemas()},
                        }
                    },
                },
                "SiteSettingsBatchResult": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["settings", "replayed"],
                    "properties": {
                        "settings": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 2,
                            "items": {"$ref": "#/components/schemas/SiteSettingResult"},
                        },
                        "replayed": {"type": "boolean"},
                    },
                },
                "SiteSettingRevisionConflict": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["key", "revision"],
                    "properties": {
                        "key": {
                            "type": "string",
                            "enum": [
                                "site.announcement.enabled",
                                "site.announcement.message",
                            ],
                        },
                        "revision": {"type": "integer", "minimum": 0},
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
                        "event_id": {"type": ["string", "null"], "format": "uuid"},
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
                        "event_id": {"type": "string", "format": "uuid"},
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
                        "mapping_set_revision",
                        "reason_code",
                        "reason",
                        "coverage_boundary",
                        "combination_policy",
                    ],
                    "properties": {
                        "state": {"type": "string", "enum": ["mapped", "excluded"]},
                        "event_id": {"type": ["string", "null"], "format": "uuid"},
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
                        "event_id",
                        "canonical_slug",
                        "complete",
                        "count",
                        "total_revision",
                        "contributions",
                    ],
                    "properties": {
                        "event_id": {"type": "string", "format": "uuid"},
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
                "EventIdentity": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "id",
                        "public_id",
                        "public_url",
                        "title",
                        "slug",
                        "canonical_path",
                        "registration_path",
                        "aliases",
                        "provenance",
                    ],
                    "properties": {
                        "id": {"type": "string", "format": "uuid"},
                        "public_id": {"type": "integer", "minimum": 1, "readOnly": True},
                        "public_url": {"type": "string", "format": "uri", "readOnly": True},
                        "title": {"type": "string", "minLength": 1},
                        "slug": {"type": "string", "minLength": 1},
                        "canonical_path": {
                            "type": "string",
                            "pattern": "^/events/[1-9][0-9]*/[-a-z0-9]+$",
                            "readOnly": True,
                        },
                        "registration_path": {
                            "type": "string",
                            "pattern": "^/events/[1-9][0-9]*/[-a-z0-9]+/register$",
                            "readOnly": True,
                        },
                        "aliases": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["path", "kind", "reason"],
                                "properties": {
                                    "path": {"type": "string", "pattern": "^/events/"},
                                    "kind": {"type": "string"},
                                    "reason": {"type": "string"},
                                },
                            },
                        },
                        "provenance": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "repository",
                                "revision",
                                "source_key",
                                "source_path",
                                "source_checksum",
                            ],
                            "properties": {
                                "repository": {"type": "string"},
                                "revision": {"type": "string"},
                                "source_key": {"type": "string"},
                                "source_path": {"type": "string"},
                                "source_checksum": {
                                    "type": "string",
                                    "pattern": "^[0-9a-f]{64}$",
                                },
                            },
                        },
                    },
                },
                "EventIdentityList": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["items", "page", "page_size", "total_count"],
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/EventIdentity"},
                        },
                        "page": {"type": "integer", "minimum": 1},
                        "page_size": {"type": "integer", "minimum": 1, "maximum": 100},
                        "total_count": {"type": "integer", "minimum": 0},
                    },
                },
                "CourseRegistrationCountImport": {
                    "type": "object",
                    "additionalProperties": True,
                    "required": [
                        "id",
                        "adapter_version",
                        "schema_version",
                        "count_policy_version",
                        "source_checksum",
                        "source_byte_size",
                        "schema_checksum",
                        "manifest_checksum",
                        "captured_at",
                        "source_frozen_at",
                        "campaign_total",
                        "row_total",
                        "state",
                        "revision",
                    ],
                    "properties": {
                        "id": {"type": "string", "format": "uuid"},
                        "adapter_version": {"type": "string"},
                        "schema_version": {"type": "string"},
                        "count_policy_version": {"type": "string"},
                        "source_checksum": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                        "source_byte_size": {"type": "integer", "minimum": 1},
                        "schema_checksum": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                        "manifest_checksum": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                        "captured_at": {"type": "string", "format": "date-time"},
                        "source_frozen_at": {"type": "string", "format": "date-time"},
                        "campaign_total": {"type": "integer", "minimum": 0},
                        "row_total": {"type": "integer", "minimum": 0},
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
                        "reason_code": {"type": "string"},
                        "created_at": {"type": "string", "format": "date-time"},
                        "updated_at": {"type": "string", "format": "date-time"},
                        "replayed": {"type": "boolean"},
                    },
                },
                "CourseRegistrationCountImportDetail": {
                    "allOf": [
                        {"$ref": "#/components/schemas/CourseRegistrationCountImport"},
                        {
                            "type": "object",
                            "required": ["counts"],
                            "properties": {
                                "counts": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "required": [
                                            "id",
                                            "campaign_slug",
                                            "cohort_slug",
                                            "baseline_count",
                                            "coverage_cutoff_at",
                                            "native_start_at",
                                            "aggregate_checksum",
                                            "state",
                                            "revision",
                                            "reason_code",
                                        ],
                                        "properties": {
                                            "id": {"type": "string", "format": "uuid"},
                                            "campaign_slug": {"type": "string"},
                                            "cohort_slug": {"type": "string"},
                                            "baseline_count": {"type": "integer", "minimum": 0},
                                            "coverage_cutoff_at": {
                                                "type": "string",
                                                "format": "date-time",
                                            },
                                            "native_start_at": {
                                                "type": "string",
                                                "format": "date-time",
                                            },
                                            "aggregate_checksum": {
                                                "type": "string",
                                                "pattern": "^[0-9a-f]{64}$",
                                            },
                                            "state": {"type": "string"},
                                            "revision": {"type": "integer", "minimum": 1},
                                            "reason_code": {"type": "string"},
                                        },
                                    },
                                }
                            },
                        },
                    ]
                },
                "CourseRegistrationCountImportList": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["items", "page", "page_size", "total_count"],
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/CourseRegistrationCountImport"},
                        },
                        "page": {"type": "integer", "minimum": 1},
                        "page_size": {"type": "integer", "minimum": 1, "maximum": 100},
                        "total_count": {"type": "integer", "minimum": 0},
                    },
                },
                "CourseRegistrationCountImportCreateRequest": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["source_reference", "confirmed", "reason_code"],
                    "properties": {
                        "source_reference": {
                            "type": "string",
                            "pattern": "^[a-z][a-z0-9_.:-]{0,127}$",
                            "writeOnly": True,
                        },
                        "confirmed": {"type": "boolean", "const": True},
                        "reason_code": {
                            "type": "string",
                            "pattern": "^[a-z][a-z0-9_]{0,63}$",
                        },
                    },
                },
                "CourseRegistrationCountActionRequest": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["confirmed", "reason_code"],
                    "properties": {
                        "confirmed": {"type": "boolean", "const": True},
                        "reason_code": {
                            "type": "string",
                            "pattern": "^[a-z][a-z0-9_]{0,63}$",
                        },
                    },
                },
                "CourseRegistrationCountActionResult": {
                    "type": "object",
                    "additionalProperties": True,
                    "required": ["run_id", "state", "revision", "replayed"],
                    "properties": {
                        "run_id": {"type": "string", "format": "uuid"},
                        "state": {"type": "string"},
                        "revision": {"type": "integer", "minimum": 1},
                        "replayed": {"type": "boolean"},
                    },
                },
                "CourseRegistrationPublicCount": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "campaign_slug",
                        "cohort_slug",
                        "complete",
                        "count",
                        "total_revision",
                        "mode",
                        "baseline_count",
                        "native_count",
                    ],
                    "properties": {
                        "campaign_slug": {"type": "string"},
                        "cohort_slug": {"type": "string"},
                        "complete": {"type": "boolean"},
                        "count": {"type": ["integer", "null"], "minimum": 0},
                        "total_revision": {"type": ["integer", "null"], "minimum": 1},
                        "mode": {
                            "type": "string",
                            "enum": ["", "baseline_plus_native", "rows_only"],
                        },
                        "baseline_count": {"type": ["integer", "null"], "minimum": 0},
                        "native_count": {"type": ["integer", "null"], "minimum": 0},
                    },
                },
                "SiteNavigationEntry": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["key", "label", "target", "position", "visible"],
                    "properties": {
                        "key": {
                            "type": "string",
                            "pattern": "^[a-z][a-z0-9_]{0,31}$",
                        },
                        "label": {"type": "string", "minLength": 1, "maxLength": 80},
                        "target": {
                            "type": "string",
                            "enum": [
                                "home",
                                "events",
                                "course_list",
                                "articles",
                                "podcast",
                                "wiki-home",
                                "books",
                                "docs-home",
                                "faq-home",
                                "slack",
                            ],
                        },
                        "position": {"type": "integer", "minimum": 1, "maximum": 12},
                        "visible": {"type": "boolean"},
                    },
                },
                "SiteNavigation": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["menu", "source", "revision", "entries"],
                    "properties": {
                        "menu": {"type": "string", "const": "primary"},
                        "source": {
                            "type": "string",
                            "enum": ["code_default", "studio", "admin_api"],
                        },
                        "revision": {"type": "integer", "minimum": 0},
                        "entries": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 12,
                            "items": {"$ref": "#/components/schemas/SiteNavigationEntry"},
                        },
                    },
                },
                "SiteNavigationReplaceRequest": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["entries"],
                    "properties": {
                        "entries": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 12,
                            "items": {"$ref": "#/components/schemas/SiteNavigationEntry"},
                        }
                    },
                },
                "SiteNavigationCommandResult": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "menu",
                        "source",
                        "revision",
                        "entries",
                        "changed",
                        "replayed",
                    ],
                    "properties": {
                        "menu": {"type": "string", "const": "primary"},
                        "source": {
                            "type": "string",
                            "enum": ["code_default", "studio", "admin_api"],
                        },
                        "revision": {"type": "integer", "minimum": 0},
                        "entries": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 12,
                            "items": {"$ref": "#/components/schemas/SiteNavigationEntry"},
                        },
                        "changed": {"type": "boolean"},
                        "replayed": {"type": "boolean"},
                    },
                },
                "SiteNavigationRevisionConflict": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["menu", "revision"],
                    "properties": {
                        "menu": {"type": "string", "const": "primary"},
                        "revision": {"type": "integer", "minimum": 0},
                    },
                },
                "SponsorAssignment": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["placement", "position", "enabled"],
                    "properties": {
                        "placement": {"type": "string", "enum": ["events_hub"]},
                        "position": {"type": "integer", "minimum": 1},
                        "enabled": {"type": "boolean"},
                    },
                },
                "Sponsor": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "id",
                        "key",
                        "name",
                        "url",
                        "tagline",
                        "lifecycle",
                        "source",
                        "revision",
                        "assignments",
                        "created_at",
                        "updated_at",
                    ],
                    "properties": {
                        "id": {"type": "string", "format": "uuid"},
                        "key": {
                            "type": "string",
                            "pattern": "^[a-z0-9][a-z0-9-]{0,63}$",
                        },
                        "name": {"type": "string", "minLength": 1, "maxLength": 120},
                        "url": {"type": "string", "maxLength": 500},
                        "tagline": {"type": "string", "maxLength": 200},
                        "lifecycle": {
                            "type": "string",
                            "enum": ["draft", "active", "archived"],
                        },
                        "source": {"type": "string", "enum": ["studio", "admin_api"]},
                        "revision": {"type": "integer", "minimum": 1},
                        "assignments": {
                            "type": "array",
                            "maxItems": 1,
                            "items": {"$ref": "#/components/schemas/SponsorAssignment"},
                        },
                        "created_at": {"type": "string", "format": "date-time"},
                        "updated_at": {"type": "string", "format": "date-time"},
                    },
                },
                "SponsorList": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["items", "page", "page_size", "total_count"],
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/Sponsor"},
                        },
                        "page": {"type": "integer", "minimum": 1},
                        "page_size": {"type": "integer", "minimum": 1, "maximum": 100},
                        "total_count": {"type": "integer", "minimum": 0},
                    },
                },
                "SponsorCommandResult": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["sponsor", "replayed"],
                    "properties": {
                        "sponsor": {"$ref": "#/components/schemas/Sponsor"},
                        "replayed": {"type": "boolean"},
                    },
                },
                "SponsorCreateRequest": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["key", "name", "lifecycle", "assignments"],
                    "properties": {
                        "key": {
                            "type": "string",
                            "pattern": "^[a-z0-9][a-z0-9-]{0,63}$",
                        },
                        "name": {"type": "string", "minLength": 1, "maxLength": 120},
                        "url": {"type": "string", "maxLength": 500},
                        "tagline": {"type": "string", "maxLength": 200},
                        "lifecycle": {"type": "string", "enum": ["draft", "active"]},
                        "assignments": {
                            "type": "array",
                            "maxItems": 1,
                            "items": {"$ref": "#/components/schemas/SponsorAssignment"},
                        },
                    },
                },
                "SponsorUpdateRequest": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "lifecycle", "assignments"],
                    "properties": {
                        "name": {"type": "string", "minLength": 1, "maxLength": 120},
                        "url": {"type": "string", "maxLength": 500},
                        "tagline": {"type": "string", "maxLength": 200},
                        "lifecycle": {"type": "string", "enum": ["draft", "active"]},
                        "assignments": {
                            "type": "array",
                            "maxItems": 1,
                            "items": {"$ref": "#/components/schemas/SponsorAssignment"},
                        },
                        "expected_revision": {"type": "integer", "minimum": 1},
                    },
                },
                "SponsorActionRequest": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["confirmed"],
                    "properties": {
                        "confirmed": {"type": "boolean", "const": True},
                        "expected_revision": {"type": "integer", "minimum": 1},
                    },
                },
                "SponsorDirectoryExportRequest": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["confirmed", "reason"],
                    "properties": {
                        "confirmed": {"type": "boolean", "const": True},
                        "reason": {"type": "string", "minLength": 1, "maxLength": 200},
                        "filters": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "lifecycle": {
                                    "type": "string",
                                    "enum": ["draft", "active", "archived"],
                                },
                                "placement": {
                                    "type": "string",
                                    "enum": ["events_hub"],
                                },
                            },
                        },
                        "lifecycle": {
                            "type": "string",
                            "enum": ["draft", "active", "archived"],
                        },
                        "placement": {"type": "string", "enum": ["events_hub"]},
                    },
                },
                "SponsorDirectoryExport": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["filename", "row_count", "csv", "replayed"],
                    "properties": {
                        "filename": {"type": "string", "const": "sponsor-directory.csv"},
                        "row_count": {"type": "integer", "minimum": 0},
                        "csv": {"type": "string"},
                        "replayed": {"type": "boolean"},
                    },
                },
                "SponsorRevisionConflict": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "revision"],
                    "properties": {
                        "id": {"type": "string", "format": "uuid"},
                        "revision": {"type": "integer", "minimum": 1},
                    },
                },
                "APIError": _error_schema(),
            },
        },
    }


def render_document() -> str:
    return json.dumps(generate_document(), indent=2, sort_keys=True) + "\n"
