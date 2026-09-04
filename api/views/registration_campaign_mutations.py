from django.core.exceptions import ValidationError

from api.safety import error_response
from api.utils import parse_json_body
from courses.models.cohort import Cohort, RegistrationCampaign
from courses.services.registration_campaigns import (
    RegistrationCampaignStateError,
    open_new_cohort,
    stop_registration,
)


CAMPAIGN_FIELDS = {
    "slug",
    "title",
    "edition_label",
    "current_course",
    "is_active",
    "marketing_markdown",
    "meta_description",
    "hero_image_url",
    "video_url",
}

# Sentinel distinguishing "current_course absent from the payload" (leave alone) from
# "current_course explicitly sent as null" (stop registration).
_CURRENT_COURSE_UNSET = object()


def clean_campaign_payload(request, *, action):
    data, err = parse_json_body(request)
    if err:
        return None, err

    err = campaign_field_error(data, action)
    if err:
        return None, err

    return normalize_campaign_data(data)


def created_campaign(data, *, actor_ref, actor_id):
    error = campaign_required_fields_error(data)
    if error:
        return None, error

    fields = dict(data)
    requested_current_course = fields.pop("current_course", _CURRENT_COURSE_UNSET)

    campaign = RegistrationCampaign(**fields)
    error = save_campaign(campaign)
    if error:
        return None, error

    # A brand-new campaign has no cohort open yet (current_course defaults to null), so
    # naming one at creation time is always a same-direction "open new cohort" -- there is
    # nothing to stop first. Routing it through the guarded service still gets it an audit
    # trail, which the old direct constructor write never had.
    if requested_current_course is not None and requested_current_course is not _CURRENT_COURSE_UNSET:
        error = _open_cohort(
            campaign,
            requested_current_course,
            actor_ref=actor_ref,
            actor_id=actor_id,
        )
        if error:
            return None, error

    return campaign, None


def apply_campaign_patch(campaign, data, *, actor_ref, actor_id):
    fields = dict(data)
    requested_current_course = fields.pop("current_course", _CURRENT_COURSE_UNSET)

    for field, value in fields.items():
        setattr(campaign, field, value)

    if fields:
        error = save_campaign(campaign)
        if error:
            return error

    if requested_current_course is not _CURRENT_COURSE_UNSET:
        error = _transition_current_course(
            campaign,
            requested_current_course,
            actor_ref=actor_ref,
            actor_id=actor_id,
        )
        if error:
            return error

    return None


def _open_cohort(campaign, cohort, *, actor_ref, actor_id):
    try:
        open_new_cohort(campaign, cohort, actor_ref=actor_ref, actor_id=actor_id)
    except RegistrationCampaignStateError as exc:
        return error_response(
            str(exc),
            "invalid_registration_campaign_state",
            details={"current_course": cohort.slug},
        )
    campaign.current_course = cohort
    return None


def _transition_current_course(campaign, requested_cohort, *, actor_ref, actor_id):
    """Route a CMP-supplied ``current_course`` value through the guarded services.

    CMP's PATCH payload is an upsert-style resync: it may repeat the value the campaign
    already has (a no-op we must not reject) or it may ask for a genuine transition, which
    only the guarded ``stop_registration``/``open_new_cohort`` services are allowed to make.
    """

    requested_id = requested_cohort.pk if requested_cohort is not None else None
    if requested_id == campaign.current_course_id:
        campaign.current_course = requested_cohort
        return None

    if requested_cohort is None:
        try:
            stop_registration(campaign, actor_ref=actor_ref, actor_id=actor_id)
        except RegistrationCampaignStateError as exc:
            return error_response(
                str(exc),
                "invalid_registration_campaign_state",
                details={"current_course": None},
            )
        campaign.current_course = None
        return None

    return _open_cohort(campaign, requested_cohort, actor_ref=actor_ref, actor_id=actor_id)


def normalize_campaign_data(data):
    result = data.copy()

    if "current_course" in result:
        slug = result.pop("current_course")
        if slug in ("", None):
            result["current_course"] = None
        else:
            try:
                result["current_course"] = Cohort.objects.get(slug=slug)
            except Cohort.DoesNotExist:
                error = error_response(
                    f"Course with slug '{slug}' does not exist",
                    "course_not_found",
                    details={"current_course": slug},
                )
                return None, error

    return result, None


def save_campaign(campaign):
    try:
        campaign.full_clean()
    except ValidationError as exc:
        return validation_error_response(exc)

    campaign.save()
    return None


def validation_error_response(exc):
    if hasattr(exc, "message_dict"):
        details = exc.message_dict
    else:
        details = {"errors": exc.messages}
    return error_response(
        "Registration campaign validation failed",
        "validation_error",
        details=details,
    )


def campaign_field_error(data, action):
    unknown_fields = set(data) - CAMPAIGN_FIELDS
    if not unknown_fields:
        return None

    field = sorted(unknown_fields)[0]
    return error_response(
        f"Cannot {action} field: {field}",
        "invalid_field",
        details={"field": field},
    )


def campaign_required_fields_error(data):
    title = data.get("title")
    slug = data.get("slug")
    if title and slug:
        return None

    error = error_response(
        "title and slug are required",
        "missing_required_fields",
    )
    return error
