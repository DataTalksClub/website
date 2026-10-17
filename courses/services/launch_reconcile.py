"""Changing template selection or destination disarms affected generated work."""

from courses.models import Announcement, Cohort, CohortAnnouncementPlan
from courses.services.launch_generation import SETTLED, generate, selected_versions
from courses.services.launch_validation import load_row


def reconcile_assignment(assignment, *, destination_changed=False):
    cohorts = Cohort.objects.all()
    own_plan = isinstance(assignment, CohortAnnouncementPlan)
    if own_plan:
        cohorts = cohorts.filter(pk=assignment.cohort_id)
    else:
        cohorts = cohorts.filter(course=assignment.course)
    for identity in cohorts.order_by("pk").values_list("pk", flat=True):
        cohort = load_row(Cohort, identity, lock=True)
        plan = CohortAnnouncementPlan.objects.filter(cohort=cohort).first()
        if plan is not None:
            plan = load_row(CohortAnnouncementPlan, plan.pk, lock=True)
        if destination_changed:
            disarm_telegram(cohort)
        if plan is None or not plan.launched or (not own_plan and plan.override_defaults):
            continue
        reconcile_plan(plan)


def disarm_telegram(cohort):
    rows = Announcement.objects.filter(cohort=cohort, channel="telegram")
    for identity in rows.order_by("pk").values_list("pk", flat=True):
        row = load_row(Announcement, identity, lock=True)
        if row.status in SETTLED or row.status == "cancelled":
            continue
        row.status = "draft"
        row.reason_code = "destination_changed"
        row.approved_revision = None
        row.revision += 1
        row.save(update_fields=["status", "reason_code", "approved_revision", "revision"])


def reconcile_plan(plan):
    selected = [version.pk for version in selected_versions(plan.cohort)]
    obsolete = Announcement.objects.filter(cohort=plan.cohort, generated_key__isnull=False).exclude(
        version_id__in=selected
    )
    for identity in obsolete.order_by("pk").values_list("pk", flat=True):
        row = load_row(Announcement, identity, lock=True)
        if row.status in SETTLED:
            continue
        row.status = "cancelled"
        row.approved_revision = None
        row.revision += 1
        row.save(update_fields=["status", "approved_revision", "revision"])
    if plan.cohort.delivery_mode == "live" and plan.cohort.curriculum_source == "current":
        generate(plan.cohort)
