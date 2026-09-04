"""The public course registration count: a plain, current-state aggregate.

There is no versioned history here.  A campaign that needed a legacy backfill
(registrations that happened before this database had ``CourseRegistration``
rows for it, e.g. imported once from CMP) records that fact as plain fields
on :class:`~courses.models.cohort.RegistrationCampaign`:

* ``registration_baseline_cohort`` -- which cohort the baseline below was
  recorded for.  A campaign can rotate ``current_course`` to a new edition
  (CMP repoints it, or a finished cohort's page keeps registering through
  the same campaign for whatever runs next); a baseline recorded for a
  previous edition never carries onto a new one, so it only applies while
  this still names the campaign's current cohort.
* ``registration_baseline_count`` -- the fixed historical figure, recorded
  once.  Zero when there is nothing to backfill.
* ``registration_native_start_at`` -- the instant at which native
  ``CourseRegistration`` rows became the complete record.  Rows before that
  instant would double-count the baseline, so they are excluded; unset when
  there is no baseline to protect against.

The public count is always ``baseline + count(native rows for the campaign's
current cohort)``, computed live.  Nothing here tracks who changed a baseline,
when, or why -- that is out of scope for a number this simple.
"""

from __future__ import annotations

from dataclasses import dataclass

from courses.models import CourseRegistration, RegistrationCampaign


@dataclass(frozen=True, slots=True)
class PublicCourseRegistrationCount:
    count: int


def public_course_registration_count(
    campaign: RegistrationCampaign,
) -> PublicCourseRegistrationCount | None:
    """The registration total for whichever cohort this campaign currently promotes.

    Returns ``None`` when the campaign has no current cohort -- there is
    nothing to count once a campaign stops promoting a specific edition.
    """

    cohort = campaign.current_course
    if cohort is None:
        return None
    baseline_count = 0
    native_start_at = None
    if campaign.registration_baseline_cohort_id == cohort.id:
        baseline_count = campaign.registration_baseline_count
        native_start_at = campaign.registration_native_start_at
    native_rows = CourseRegistration.objects.filter(campaign=campaign, course=cohort)
    if native_start_at is not None:
        native_rows = native_rows.filter(created_at__gte=native_start_at)
    return PublicCourseRegistrationCount(count=baseline_count + native_rows.count())
