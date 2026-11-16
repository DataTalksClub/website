"""Allowlisted OpenAPI schemas; never describe or serialize provider secrets."""


def _object(properties, required=None):
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties) if required is None else required,
        "properties": properties,
    }


def _reference(name):
    return {"$ref": f"#/components/schemas/{name}"}


def slack_schemas():
    identifier = {"type": "integer", "minimum": 1}
    nullable_identifier = {"type": ["integer", "null"], "minimum": 1}
    uuid_identifier = {"type": "string", "format": "uuid"}
    nullable_uuid = {"type": ["string", "null"], "format": "uuid"}
    nullable_date = {"type": ["string", "null"], "format": "date-time"}
    summary = {
        "id": uuid_identifier,
        "user_id": identifier,
        "sponsor_id": nullable_uuid,
        "state": {
            "type": "string",
            "enum": ["pending", "approved", "joined", "rejected", "banned"],
        },
        "approval_source": {"type": "string", "enum": ["", "course", "staff"]},
    }
    preview = {
        "member_id": uuid_identifier,
        "affected_count": {"type": "integer", "minimum": 1},
        "scope_token": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    }
    return {
        "SlackApplicationSummary": _object(summary),
        "SlackApplication": _object(
            {
                **summary,
                "who": {"type": "string", "maxLength": 500},
                "work": {"type": "string", "maxLength": 500},
                "what": {"type": "string", "maxLength": 2000},
            }
        ),
        "SlackApplications": _object(
            {
                "applications": {
                    "type": "array",
                    "maxItems": 100,
                    "items": _reference("SlackApplication"),
                },
                "next_after": nullable_identifier,
            }
        ),
        "SlackReviewRequest": _object(
            {
                "action": {"type": "string", "enum": ["approve", "reject"]},
                "expected_state": {"type": "string", "const": "pending"},
            }
        ),
        "SlackReviewResult": _object(
            {
                "application": _reference("SlackApplicationSummary"),
                "replayed": {"type": "boolean"},
            }
        ),
        "SlackBanPreview": _object(preview),
        "SlackBanRequest": _object(
            {
                "confirmed": {"type": "boolean", "const": True},
                "expected_count": {"type": "integer", "minimum": 1, "maximum": 200000},
                "scope_token": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            }
        ),
        "SlackBanResult": _object(
            {
                "member_id": uuid_identifier,
                "affected_count": {"type": "integer", "minimum": 1},
                "replayed": {"type": "boolean"},
            }
        ),
        "SlackJoinObservation": _object(
            {
                "id": identifier,
                "slack_user_id": {"type": "string", "maxLength": 32},
                "inviter_slack_id": {"type": "string", "maxLength": 32},
                "classification": {
                    "type": "string",
                    "enum": ["website_approved", "outside_flow", "unknown"],
                },
                "application_id": nullable_uuid,
                "sponsor_id": nullable_uuid,
                "observed_at": {"type": "string", "format": "date-time"},
                "joined_at": nullable_date,
            }
        ),
        "SlackJoinReport": _object(
            {
                "joins": {
                    "type": "array",
                    "maxItems": 100,
                    "items": _reference("SlackJoinObservation"),
                },
                "next_after": nullable_identifier,
                "monitor": _object(
                    {"baseline_at": nullable_date, "last_success_at": nullable_date}
                ),
            }
        ),
    }


def slack_parameters(key, parameters):
    if key.startswith("slack."):
        parameters = [
            {**parameter, "schema": {"type": "string", "minLength": 1, "maxLength": 128}}
            if parameter.get("name") == "Idempotency-Key"
            else parameter
            for parameter in parameters
        ]
    if key == "slack.applications.review":
        return [
            *parameters,
            {
                "name": "application_id",
                "in": "path",
                "required": True,
                "schema": {"type": "string", "format": "uuid"},
            },
        ]
    if key in {"slack.members.preview", "slack.members.ban"}:
        return [
            *parameters,
            {
                "name": "member_id",
                "in": "path",
                "required": True,
                "schema": {"type": "string", "format": "uuid"},
            },
        ]
    if key not in {"slack.applications.list", "slack.joins.report"}:
        return parameters
    selector = "state" if key == "slack.applications.list" else "classification"
    return [
        *parameters,
        {"name": "after", "in": "query", "schema": {"type": "integer", "minimum": 0}},
        {
            "name": "limit",
            "in": "query",
            "schema": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        {"name": selector, "in": "query", "schema": {"type": "string"}},
    ]
