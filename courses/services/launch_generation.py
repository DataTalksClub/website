"""Launch creates reviewable, unarmed rows; it never dispatches a message."""

from datetime import timedelta

from django.utils import timezone

from courses.models import Announcement, AnnouncementAssignment, Cohort, CohortAnnouncementPlan
from courses.services.launch_context import announcement_context
from courses.services.launch_validation import live_cohort, load_row, serialize_rows

SETTLED = frozenset({"dispatching", "submitted", "ambiguous"})


def selected_versions(cohort):
    plan, _ = CohortAnnouncementPlan.objects.get_or_create(cohort=cohort)
    if plan.override_defaults:
        return plan.versions.all()
    assignment = AnnouncementAssignment.objects.filter(course=cohort.course).first()
    if assignment is None:
        return []
    return assignment.versions.all()


def targets(cohort, kind):
    if kind == "cohort":
        return [("cohort", cohort)]
    if kind == "project":
        return [("project", row) for row in cohort.project_set.order_by("pk")]
    if cohort.curriculum_format == "shared":
        return [
            ("shared_module_placement", row)
            for row in cohort.shared_module_placements.select_related(
                "shared_module", "terminal_homework"
            )
        ]
    return [("module", row) for row in cohort.modules.select_related("terminal_homework")]


def generate(cohort):
    live_cohort(cohort)
    serialize_rows(Cohort.objects.filter(pk=cohort.pk))
    plan, _ = CohortAnnouncementPlan.objects.get_or_create(cohort=cohort)
    plan.launched = True
    plan.revision += 1
    plan.save(update_fields=["launched", "revision"])
    count = 0
    for version in selected_versions(cohort):
        for kind, target in targets(cohort, version.kind):
            key = f"{cohort.pk}:{version.pk}:{kind}:{target.pk}"
            row, created = Announcement.objects.get_or_create(
                generated_key=key,
                defaults={
                    "cohort": cohort,
                    "version": version,
                    "channel": version.channel,
                    "target_kind": kind,
                    "target_id": str(target.pk),
                    "subject": version.subject,
                    "body": version.body,
                },
            )
            if created:
                refresh_row(row, target.starts_at)
                count += 1
    return {"cohort_id": str(cohort.pk), "generated": count, "armed": 0}


def refresh_row(row, anchor):
    propose_schedule(row, anchor)
    row.context = announcement_context(row.target_kind, row.target_id, row.context_overrides)
    row.status = "draft"
    row.approved_revision = None
    row.revision += 1
    row.save()


def propose_schedule(row, anchor):
    row.anchor_at = anchor
    row.scheduled_at = None
    row.reason_code = "missing_start"
    if anchor is not None:
        row.scheduled_at = anchor + timedelta(minutes=row.version.offset_minutes)
        row.reason_code = ""
        if row.scheduled_at <= timezone.now():
            row.reason_code = "past_due"


def refresh_generated(cohort):
    from courses.services.launch_timing import timing_owner

    rows = Announcement.objects.filter(cohort=cohort, generated_key__isnull=False)
    for identity in rows.order_by("pk").values_list("pk", flat=True):
        row = load_row(Announcement, identity, lock=True)
        if row.status in SETTLED or row.status == "cancelled":
            continue
        target = timing_owner(row.target_kind, row.target_id)
        if row.anchor_at != target.starts_at:
            refresh_row(row, target.starts_at)
