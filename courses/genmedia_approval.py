"""Approval policy and nightly automation for the Production AI Agents campaign."""

from __future__ import annotations

import csv
from pathlib import Path
from zoneinfo import ZoneInfo

from community_base.jobs import schedule
from community_base.jobs.registry import JobContext, JobPayload, register_handler
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from courses.genmedia_policy import (
    AUTO_APPROVAL_HOUR,
    AUTO_APPROVAL_LIMIT,
    CAMPAIGN_SLUG,
    excluded_reason,
)
from courses.genmedia_policy import (
    AUTO_APPROVAL_TIMEZONE as AUTO_APPROVAL_TIMEZONE_NAME,
)

AUTO_APPROVAL_TIMEZONE = ZoneInfo(AUTO_APPROVAL_TIMEZONE_NAME)
AUTO_APPROVAL_SCHEDULE = "courses.genmedia-nightly-approval"
AUTO_APPROVAL_HANDLER = "courses.genmedia-nightly-approval"


def preview_path(local_date) -> Path:
    directory = Path(settings.BASE_DIR) / ".tmp" / "genmedia-approval-previews"
    return directory / f"approved-{local_date.isoformat()}.csv"


def _nightly_window(now=None):
    current = now or timezone.now()
    return timezone.localtime(current, AUTO_APPROVAL_TIMEZONE)


def approve_pending_registrations(*, now=None, output_path=None):
    """Approve eligible pending applicants and write that night's private CSV."""

    from courses.models import CourseRegistration, RegistrationCampaign

    local_now = _nightly_window(now)
    if local_now.hour != AUTO_APPROVAL_HOUR:
        return {"approved": 0, "excluded": 0, "stopped_at_limit": False, "path": None}
    destination = Path(output_path) if output_path else preview_path(local_now.date())

    with transaction.atomic():
        campaign = (
            RegistrationCampaign.objects.select_for_update()
            .filter(slug=CAMPAIGN_SLUG)
            .first()
        )
        if campaign is None:
            return {"approved": 0, "excluded": 0, "stopped_at_limit": False, "path": None}

        registrations = list(
            CourseRegistration.objects.select_for_update()
            .filter(
                campaign=campaign,
                course_id=campaign.current_course_id,
                status=CourseRegistration.Status.PENDING,
            )
            .order_by("created_at", "pk")
        )
        total = CourseRegistration.objects.filter(
            campaign=campaign, course_id=campaign.current_course_id
        ).count()
        if total >= AUTO_APPROVAL_LIMIT:
            return {
                "approved": 0,
                "excluded": sum(bool(excluded_reason(row)) for row in registrations),
                "stopped_at_limit": True,
                "path": None,
            }

        eligible = [row for row in registrations if not excluded_reason(row)]
        approved_at = timezone.now()
        for registration in eligible:
            registration.status = CourseRegistration.Status.APPROVED
            registration.auto_approved_at = approved_at
            registration.updated_at = approved_at
        CourseRegistration.objects.bulk_update(
            eligible, ["status", "auto_approved_at", "updated_at"]
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(
                output,
                fieldnames=("name", "email", "country", "ukraine_region", "approved_at"),
            )
            writer.writeheader()
            for registration in eligible:
                writer.writerow(
                    {
                        "name": registration.name,
                        "email": registration.email,
                        "country": registration.country,
                        "ukraine_region": registration.get_ukraine_region_display(),
                        "approved_at": local_now.isoformat(),
                    }
                )
    return {
        "approved": len(eligible),
        "excluded": len(registrations) - len(eligible),
        "stopped_at_limit": False,
        "path": str(destination),
    }


@register_handler(AUTO_APPROVAL_HANDLER)
def dispatch_nightly_approval(context: JobContext, payload: JobPayload) -> None:
    del context, payload
    approve_pending_registrations()


# UTC 18:00 and 19:00 cover 20:00 Europe/Berlin in both DST seasons. The
# handler's local-time guard makes exactly one of the two invocations active.
schedule(
    AUTO_APPROVAL_HANDLER,
    cron="0 18,19 * * *",
    payload={},
    name=AUTO_APPROVAL_SCHEDULE,
)
