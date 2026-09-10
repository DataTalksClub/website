from dataclasses import dataclass

from django.http import JsonResponse

from accounts.navigation import can_access_course_studio
from accounts.studio_roles import STUDIO_ACCESS
from api.utils import parse_date
from management_auth.models import APIPrincipal
from management_auth.services import principal_has_permission

# The one scope that authorizes compatibility-API course operations.  It is
# deliberately separate from every management-API capability key: a credential
# minted for reading studio health must not silently operate course data here.
STAFF_OPERATION_SCOPE = "compatibility.course_operations"


@dataclass(frozen=True)
class PatchFieldRules:
    allowed_fields: set[str]
    valid_states: set[str]
    invalid_state_code: str
    date_fields: set[str]


@dataclass(frozen=True)
class DeleteObjectData:
    instance: object
    closed_state: str
    related_queryset: object
    related_name: str
    noun: str


def error_response(message, code, status=400, details=None):
    data = {"error": message, "code": code}
    if details:
        data["details"] = details
    response = JsonResponse(data, status=status)
    return response


def require_staff_token(request):
    """Require course-operations authority from a scoped management credential.

    Legacy raw ``Token`` keys keep learner-level compatibility access but no
    longer carry management authority (audit BE-02): staff operations need an
    authenticated ``management_auth.APICredential`` riding on the request.
    Human principals pass the same course-studio role gate as Studio course
    operations; service principals need the explicit Studio-access permission.
    Either way the credential must carry the compatibility course-operations
    scope, so a read-only or unrelated management credential fails closed.
    """
    identity = getattr(request, "management_identity", None)
    if identity is None:
        return error_response(
            "Staff token required",
            "staff_token_required",
            status=403,
        )
    principal = identity.principal
    if STAFF_OPERATION_SCOPE not in identity.credential.scopes:
        return error_response(
            "Staff token required",
            "staff_token_required",
            status=403,
        )
    if principal.kind == APIPrincipal.Kind.SERVICE:
        allowed = principal_has_permission(principal, STUDIO_ACCESS)
    else:
        allowed = principal.user is not None and can_access_course_studio(principal.user)
    if not allowed:
        return error_response(
            "Staff token required",
            "staff_token_required",
            status=403,
        )
    return None


def ensure_closed_for_delete(instance, closed_state, noun):
    if instance.state != closed_state:
        return error_response(
            f"Only closed {noun}s can be deleted",
            f"{noun}_not_closed",
            details={"state": instance.state},
        )
    return None


def ensure_no_related_records_for_delete(queryset, related_name, noun):
    count = queryset.count()
    if count > 0:
        return error_response(
            f"Cannot delete {noun} with existing {related_name}",
            f"{noun}_has_{related_name}",
            details={f"{related_name}_count": count},
        )
    return None


def delete_object_or_error(data):
    """Delete instance if it's closed and has no related records.

    Returns the success JsonResponse, or an error response if a guard fails.
    """
    err = ensure_closed_for_delete(
        data.instance,
        data.closed_state,
        data.noun,
    )
    if err:
        return err

    err = ensure_no_related_records_for_delete(
        data.related_queryset,
        data.related_name,
        data.noun,
    )
    if err:
        return err

    data.instance.delete()
    payload = {"deleted": True}
    response = JsonResponse(payload)
    return response


def apply_patch_fields(
    instance,
    data,
    rules,
):
    """Apply a PATCH payload field-by-field with validation.

    Rejects unknown fields, validates ``state`` against ``valid_states`` and
    parses ``date_fields``. Returns an error response on the first invalid
    field, else None (mutating ``instance`` in place).
    """
    for field, value in data.items():
        error = apply_patch_field(instance, field, value, rules)
        if error:
            return error

    return None


def apply_patch_field(instance, field, value, rules):
    error = validate_patch_field(field, value, rules)
    if error:
        return error

    parsed_value, error = parse_patch_field_value(field, value, rules)
    if error:
        return error

    setattr(instance, field, parsed_value)
    return None


def validate_patch_field(
    field,
    value,
    rules,
):
    if field not in rules.allowed_fields:
        return error_response(
            f"Cannot update field: {field}",
            "invalid_field",
            details={"field": field},
        )

    if field == "state" and value not in rules.valid_states:
        valid_states = sorted(rules.valid_states)
        return error_response(
            f"Invalid state. Must be one of: {valid_states}",
            rules.invalid_state_code,
            details={"valid_states": valid_states},
        )

    return None


def parse_patch_field_value(field, value, rules):
    if field not in rules.date_fields:
        return value, None

    parsed_value = parse_date(value)
    if parsed_value is None:
        details = {"field": field}
        error = error_response(
            f"Invalid date format for {field}",
            "invalid_date_format",
            details=details,
        )
        return None, error

    return parsed_value, None
