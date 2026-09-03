"""Which registration campaign a cohort registers through.

Registration always happens on this site.  A cohort reaches its campaign through one of
two bindings, and the course management platform publishes both:

* ``RegistrationCampaign.current_course`` -- the edition the campaign is promoting *now*.
  CMP clears it when that edition stops taking registrations, so this is the binding that
  means "register for this edition".
* ``Cohort.registration_url`` -- the campaign page the cohort's own row names, of the form
  ``https://courses.datatalks.club/register/<campaign-slug>/``.  It outlives
  ``current_course``, so it is the binding that means "this course registers here", and it
  is what turns a finished edition's dead external link into an internal
  "register for the next edition".

Nothing is derived.  A registration URL that is not a campaign path, or that names a
campaign this database does not hold, resolves to nothing: the page then offers no
registration at all rather than guessing at one.  ``mlops-zoomcamp-2025`` is exactly that
case -- finished, with no successor and no campaign -- and it must stay silent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from django.db import transaction

from core.audit import AuditWriteContext, record_audit_event
from core.models import AuditEvent
from core.services import ServiceContext, validate_actor_ref
from courses.models.cohort import Cohort, Course, RegistrationCampaign

__all__ = [
    "FamilyRegistration",
    "RegistrationCampaignStateError",
    "active_campaign_for_cohort",
    "campaign_slug_in_registration_url",
    "family_registration",
    "next_edition_campaign_for_cohort",
    "open_new_cohort",
    "stop_registration",
]

# CMP's own public registration path.  The host is deliberately unconstrained: what is
# read is the campaign slug the platform published, not the origin it published it on.
_CAMPAIGN_PATH = re.compile(r"^https?://[^/]+/register/(?P<slug>[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)/?$")


def campaign_slug_in_registration_url(registration_url: str) -> str:
    """Return the campaign slug a CMP registration URL names, or an empty string."""

    if not registration_url:
        return ""
    match = _CAMPAIGN_PATH.match(registration_url.strip())
    if match is None:
        return ""
    return match.group("slug")


def active_campaign_for_cohort(cohort: Cohort) -> RegistrationCampaign | None:
    """Return the active campaign promoting this exact edition, if there is one."""

    return (
        RegistrationCampaign.objects.filter(current_course=cohort, is_active=True)
        .order_by("id")
        .first()
    )


def next_edition_campaign_for_cohort(cohort: Cohort) -> RegistrationCampaign | None:
    """Return the campaign this cohort's course registers through, for a closed edition.

    Only meaningful when no campaign promotes this edition: it is the waiting list for
    whatever runs next.  A campaign that *is* promoting this edition is returned by
    :func:`active_campaign_for_cohort` instead, so this never duplicates it.
    """

    slug = campaign_slug_in_registration_url(cohort.registration_url)
    if not slug:
        return None
    campaign = RegistrationCampaign.objects.filter(slug=slug, is_active=True).first()
    if campaign is None or campaign.current_course_id == cohort.id:
        return None
    return campaign


@dataclass(frozen=True, slots=True)
class FamilyRegistration:
    """What a course family currently offers a visitor who wants to register.

    ``cohort`` is set only when a campaign is promoting that exact edition, which is the
    one case where naming an edition is honest.  A family whose editions have all closed
    keeps the campaign and drops the edition, so the page can say "the next edition"
    without naming a cohort that is over.  Both empty means offer nothing.
    """

    campaign: RegistrationCampaign | None = None
    cohort: Cohort | None = None

    def __bool__(self) -> bool:
        return self.campaign is not None


def family_registration(family: Course) -> FamilyRegistration:
    """Return the registration a course family offers, newest edition first."""

    editions = list(family.cohorts.filter(visible=True).order_by("-year", "-id"))
    for cohort in editions:
        campaign = active_campaign_for_cohort(cohort)
        if campaign is not None:
            return FamilyRegistration(campaign=campaign, cohort=cohort)
    for cohort in editions:
        campaign = next_edition_campaign_for_cohort(cohort)
        if campaign is not None:
            return FamilyRegistration(campaign=campaign)
    return FamilyRegistration()


class RegistrationCampaignStateError(ValueError):
    """The campaign's ``current_course`` state does not allow this transition.

    A campaign is either promoting one cohort (``current_course`` set) or
    promoting nothing while it waits for the next edition ("future/none",
    ``current_course`` is ``None``).  Both write operations below fail closed
    rather than silently clobbering whichever state the campaign is actually
    in.
    """


def _campaign_write_context(
    context: ServiceContext | None,
    *,
    actor_ref: str,
    actor_id: object | None,
) -> AuditWriteContext:
    validate_actor_ref(actor_ref)
    service_context = context or ServiceContext.from_current(actor_ref=actor_ref)
    return AuditWriteContext.from_service_context(
        service_context,
        actor_id=actor_id,
    )


def stop_registration(
    campaign: RegistrationCampaign,
    *,
    actor_ref: str,
    actor_id: object | None = None,
    context: ServiceContext | None = None,
    using: str = "default",
) -> RegistrationCampaign:
    """Close registration for the cohort ``campaign`` currently promotes.

    Sets ``current_course`` to ``None`` -- the campaign's "future/none" state,
    from which :func:`open_new_cohort` can later open the next edition.  Fails
    closed when the campaign has nothing open to stop, rather than treating a
    repeated click as a harmless no-op.
    """

    audit_context = _campaign_write_context(context, actor_ref=actor_ref, actor_id=actor_id)
    with transaction.atomic(using=using):
        locked = RegistrationCampaign.objects.using(using).get(pk=campaign.pk)
        previous_cohort = locked.current_course
        if previous_cohort is None:
            raise RegistrationCampaignStateError(
                "This campaign has no open cohort to stop registration for."
            )
        locked.current_course = None
        locked.save(using=using, update_fields=("current_course", "updated_at"))
        record_audit_event(
            action="courses.registration_campaign.registration_stopped",
            target_type="courses.registration_campaign",
            target_label=locked.slug,
            outcome=AuditEvent.Outcome.SUCCEEDED,
            context=audit_context,
            changes={
                "current_course_id": {"before": previous_cohort.pk, "after": None},
            },
            metadata={
                "campaign_id": locked.pk,
                "previous_cohort_slug": previous_cohort.slug,
            },
            using=using,
        )
        return locked


def open_new_cohort(
    campaign: RegistrationCampaign,
    cohort: Cohort,
    *,
    actor_ref: str,
    actor_id: object | None = None,
    context: ServiceContext | None = None,
    using: str = "default",
) -> RegistrationCampaign:
    """Open registration for ``cohort`` through ``campaign``.

    Only valid while the campaign is in the "future/none" state.  A campaign
    that is still promoting a cohort must be stopped first via
    :func:`stop_registration` -- this never silently repoints an open
    campaign out from under whoever is currently registering.
    """

    if not isinstance(cohort, Cohort) or cohort.pk is None:
        raise RegistrationCampaignStateError(
            "Choose an existing cohort to open registration for."
        )

    audit_context = _campaign_write_context(context, actor_ref=actor_ref, actor_id=actor_id)
    with transaction.atomic(using=using):
        locked = RegistrationCampaign.objects.using(using).get(pk=campaign.pk)
        if locked.current_course_id is not None:
            raise RegistrationCampaignStateError(
                "Stop registration for the current cohort before opening a new one."
            )
        locked.current_course = cohort
        locked.save(using=using, update_fields=("current_course", "updated_at"))
        record_audit_event(
            action="courses.registration_campaign.cohort_opened",
            target_type="courses.registration_campaign",
            target_label=locked.slug,
            outcome=AuditEvent.Outcome.SUCCEEDED,
            context=audit_context,
            changes={
                "current_course_id": {"before": None, "after": cohort.pk},
            },
            metadata={
                "campaign_id": locked.pk,
                "cohort_slug": cohort.slug,
            },
            using=using,
        )
        return locked
