"""OpenAPI shapes derived from the commands' writable-field allowlists."""

from courses.services.launch_commands import SEND_ACTIONS, WRITE_ACTIONS
from courses.services.launch_validation import LAUNCH_IDEMPOTENCY_KEY_MAX

INTEGER_FIELDS = frozenset({"year", "expected_revision", "offset_minutes"})
BOOLEAN_FIELDS = frozenset({"confirmed", "archived", "override_defaults"})
REQUIRED = {
    "template_save": ["name", "channel", "kind", "body"],
    "template_publish": ["id", "expected_revision"],
    "template_archive": ["id", "expected_revision", "archived"],
    "course_create": ["slug", "title"],
    "cohort_create": ["course_id", "slug", "identifier", "title", "description", "year"],
    "assignment_save": ["version_ids", "expected_revision"],
    "timing_update": ["target_kind", "id", "starts_at", "provenance", "expected_revision"],
    "launch": ["cohort_id"],
    "compose": ["cohort_id", "version_id"],
    "announcement_edit": ["id", "subject", "body", "expected_revision"],
    "approve": ["id", "expected_revision"],
    "cancel": ["id", "expected_revision"],
    "schedule": ["id", "expected_revision", "scheduled_at", "confirmed"],
    "send": ["id", "expected_revision", "confirmed"],
    "test_send": ["id", "destination", "confirmed"],
    "launch_and_schedule": ["cohort_id", "confirmed", "preview_token"],
}


def field_schema(name):
    if name in INTEGER_FIELDS:
        return {"type": "integer"}
    if name in BOOLEAN_FIELDS:
        return {"type": "boolean"}
    if name == "version_ids":
        return {
            "type": "array",
            "maxItems": 50,
            "uniqueItems": True,
            "items": {"type": "string", "format": "uuid"},
        }
    if name == "context":
        return {"type": "object", "additionalProperties": {"type": ["string", "boolean"]}}
    if name in {"starts_at", "scheduled_at"}:
        return {"type": ["string", "null"], "format": "date-time"}
    return {"type": "string"}


def launch_command_schema():
    variants = []
    for action, (_, allowed) in {**WRITE_ACTIONS, **SEND_ACTIONS}.items():
        properties = {"action": {"const": action, "type": "string"}}
        for name in sorted(allowed):
            properties[name] = field_schema(name)
        variants.append(
            {
                "type": "object",
                "required": ["action", *REQUIRED[action]],
                "additionalProperties": False,
                "properties": properties,
            }
        )
    return {"oneOf": variants}


def launch_parameters(key, parameters):
    if not key.startswith("courses.launch."):
        return parameters
    for parameter in parameters:
        if parameter["name"] == "Idempotency-Key":
            parameter["schema"]["maxLength"] = LAUNCH_IDEMPOTENCY_KEY_MAX
    if parameters:
        parameters.append(
            {
                "name": "If-Match",
                "in": "header",
                "required": False,
                "description": (
                    "Required when the action carries expected_revision; "
                    "must match that body revision."
                ),
                "schema": {"type": "string", "pattern": '^"rev-[0-9]+"$'},
            }
        )
    return parameters
