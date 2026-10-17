"""One confirmed privileged cohort action: preview, then launch-and-schedule.

Dry-run is a read-only query: it classifies every generated row plus every
prospective version/target combination, returns a global preview fingerprint and
a tamper-evident signed preview token, and performs zero writes (no generate,
approve, schedule, dispatch, or idempotency row).

Execute verifies the signed token (never trusts editable client maps), then
re-validates the fingerprint under canonical locks BEFORE generate(), then
approves and schedules each still-eligible row through the existing single-row
dispatch path. The global fingerprint binds configuration values; per-row
token records bind individual copy and timing. Never approve unseen changed
content; never enqueue unseen rows.

Row records are always LISTs with the generated key as a VALUE:
shared-curriculum keys combine integer cohort/placement ids with a UUID
version id (at most 100 characters even at BigAutoField maxima), and keys
travel only as JSON values, never as JSON object keys, so the 128-character
canonical idempotency object-key cap can never reject them.
"""

import hashlib
import json
from datetime import timedelta

from django.core.signing import BadSignature, Signer
from django.db import transaction
from django.utils import timezone

from courses.models import (
    Announcement,
    AnnouncementAssignment,
    Cohort,
    CohortAnnouncementPlan,
    Course,
)
from courses.services.launch_announcements import approve as approve_row
from courses.services.launch_announcements import schedule_announcement, serialize_announcement
from courses.services.launch_context import announcement_context, safe_preview
from courses.services.launch_generation import SETTLED, generate, targets
from courses.services.launch_timing import timing_owner
from courses.services.launch_validation import (
    LaunchError,
    iso,
    live_cohort,
    load_row,
    serialize_rows,
)

FINGERPRINT_VERSION = "launch-batch-v1"
PREVIEW_TOKEN_VERSION = 1
PREVIEW_TOKEN_SALT = "course-launch-batch-v1"
PREVIEW_ROWS_MAX = 5000
GENERATED_KEY_MAX = 180
MOMENT_MAX = 64


def effective_selection(cohort):
    """Read-only effective template selection, mirroring generate().

    Never calls selected_versions(): that helper get_or_creates a
    CohortAnnouncementPlan, which the preview/fingerprint path must not do.
    A missing plan canonicalizes to launched False, override_defaults False, and
    the plan's own version list empty — while the EFFECTIVE selection still falls
    back to the course assignment versions exactly as generate() would resolve
    them. Assignment defaults are never replaced with an empty set here.
    """
    plan = CohortAnnouncementPlan.objects.filter(cohort=cohort).first()
    assignment = AnnouncementAssignment.objects.filter(course=cohort.course).first()
    override = plan.override_defaults if plan is not None else False
    launched = plan.launched if plan is not None else False
    if plan is not None and plan.override_defaults:
        versions = list(plan.versions.order_by("pk"))
        source = "plan"
    elif assignment is not None:
        versions = list(assignment.versions.order_by("pk"))
        source = "assignment"
    else:
        versions = []
        source = "assignment"
    return {
        "source": source,
        "versions": versions,
        "launched": launched,
        "override_defaults": override,
        "destination_key": assignment.telegram_destination_key if assignment is not None else "",
    }


def fingerprint_value(cohort, selection):
    return {
        "contract": FINGERPRINT_VERSION,
        "cohort_id": str(cohort.pk),
        "course_id": str(cohort.course_id),
        "delivery_mode": cohort.delivery_mode,
        "curriculum_source": cohort.curriculum_source,
        "curriculum_format": cohort.curriculum_format,
        "launched": selection["launched"],
        "override_defaults": selection["override_defaults"],
        "source": selection["source"],
        "version_ids": sorted(str(version.pk) for version in selection["versions"]),
        "destination_key": selection["destination_key"],
    }


def fingerprint_digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def generated_key_for(cohort, version, kind, target):
    return f"{cohort.pk}:{version.pk}:{kind}:{target.pk}"


def proposed_at(anchor, version):
    if anchor is None:
        return None
    return anchor + timedelta(minutes=version.offset_minutes)


def is_future(moment, now):
    return moment is not None and moment > now


def telegram_reference(cohort):
    assignment = AnnouncementAssignment.objects.filter(course=cohort.course).first()
    if assignment is None:
        return ""
    return assignment.telegram_destination_key


def classify_row(row, timing_start, now):
    """Shared categorical truth for one row; never raises for skips."""
    if not row.generated_key:
        return "manual"
    if row.status == "cancelled":
        return "cancelled"
    if row.status in SETTLED:
        return "settled"
    if row.status == "scheduled":
        return "already_scheduled"
    if row.anchor_at is None and timing_start is None:
        return "missing_start"
    if row.anchor_at is None or timing_start is None or row.anchor_at != timing_start:
        return "start_changed"
    due = proposed_at(row.anchor_at, row.version) if row.version is not None else None
    if not is_future(due, now):
        return "past_due"
    if row.channel == "telegram" and not telegram_reference(row.cohort):
        return "blocked_destination"
    return "eligible"


def preview_batch(payload):
    """Read-only batch preview. Performs zero writes by construction."""
    cohort = load_row(Cohort, payload.get("cohort_id"))
    live_cohort(cohort)
    return build_preview(cohort)


def bound_record(key, revision, anchor, due):
    """One tamper-evident row record: a LIST with the generated key as a value."""
    return [key, revision, iso(anchor), iso(due)]


def build_preview(cohort):
    now = timezone.now()
    selection = effective_selection(cohort)
    digest = fingerprint_digest(fingerprint_value(cohort, selection))
    rows = []
    bound = []
    seen = set()
    existing = list(
        Announcement.objects.filter(cohort=cohort)
        .select_related("cohort", "version")
        .prefetch_related("deliveries__email_delivery")
        .order_by("pk")
    )
    from courses.services.launch_status import annotate_audiences

    annotate_audiences(existing)
    for row in existing:
        seen.add(row.generated_key)
        item = preview_existing(row, now)
        rows.append(item)
        if row.generated_key:
            anchor = row.anchor_at
            due = proposed_at(anchor, row.version) if row.version is not None else None
            bound.append(bound_record(row.generated_key, row.revision, anchor, due))
    for version in selection["versions"]:
        for kind, target in targets(cohort, version.kind):
            key = generated_key_for(cohort, version, kind, target)
            if key in seen:
                continue
            seen.add(key)
            item = preview_prospective(cohort, version, kind, target, key, now)
            rows.append(item)
            bound.append(
                bound_record(key, None, target.starts_at, proposed_at(target.starts_at, version))
            )
    bound.sort(key=lambda record: record[0])
    if len(bound) > PREVIEW_ROWS_MAX:
        raise LaunchError("This cohort preview is too large to confirm in one batch.")
    eligible = sum(1 for item in rows if item["reason"] == "eligible")
    guidance = None
    if not selection["versions"]:
        guidance = (
            "No published template versions are selected for this cohort. "
            "Assign versions to the course or override them on the cohort plan, "
            "then preview again."
        )
    return {
        "cohort_id": str(cohort.pk),
        "course_id": str(cohort.course_id),
        "preview_fingerprint": digest,
        "preview_token": sign_preview(digest, bound),
        "selection": {
            "source": selection["source"],
            "version_ids": sorted(str(version.pk) for version in selection["versions"]),
            "launched": selection["launched"],
            "override_defaults": selection["override_defaults"],
            "destination_key": selection["destination_key"],
        },
        "rows": rows,
        "eligible_count": eligible,
        "skipped_count": len(rows) - eligible,
        "guidance": guidance,
        "replayed": False,
    }


def preview_existing(row, now):
    try:
        timing_start = timing_owner(row.target_kind, row.target_id).starts_at
    except LaunchError:
        timing_start = "unresolvable"
    if timing_start == "unresolvable":
        reason = "start_changed"
    else:
        reason = classify_row(row, timing_start, now)
    anchor = row.anchor_at
    due = proposed_at(anchor, row.version) if row.version is not None else None
    return {
        "generated_key": row.generated_key,
        "status": "existing",
        "reason": reason,
        "announcement": serialize_announcement(row),
        "expected_revision": row.revision,
        "expected_anchor_at": iso(anchor),
        "expected_proposed_at": iso(due),
    }


def preview_prospective(cohort, version, kind, target, key, now):
    anchor = target.starts_at
    due = proposed_at(anchor, version)
    if anchor is None:
        reason = "missing_start"
    elif not is_future(due, now):
        reason = "past_due"
    elif version.channel == "telegram" and not effective_destination(cohort):
        reason = "blocked_destination"
    else:
        reason = "eligible"
    return {
        "generated_key": key,
        "status": "prospective",
        "reason": reason,
        "version_id": str(version.pk),
        "target_kind": kind,
        "target_id": str(target.pk),
        "channel": version.channel,
        "copy": prospective_copy(cohort, version, kind, target),
        "expected_revision": None,
        "expected_anchor_at": iso(anchor),
        "expected_proposed_at": iso(due),
    }


def effective_destination(cohort):
    return telegram_reference(cohort)


def prospective_copy(cohort, version, kind, target):
    try:
        context = announcement_context(kind, str(target.pk), {})
    except LaunchError as error:
        return {"subject": version.subject, "body": version.body, "error": str(error)}
    probe = Announcement(
        cohort=cohort,
        version=version,
        channel=version.channel,
        target_kind=kind,
        target_id=str(target.pk),
        subject=version.subject,
        body=version.body,
        context=context,
    )
    return safe_preview(probe)


def batch_signer():
    return Signer(salt=PREVIEW_TOKEN_SALT)


def sign_preview(digest, bound):
    """Bind the exact preview membership + row revisions/anchors/times.

    The token carries only generated keys, revisions, and ISO timestamps plus
    the global fingerprint: no bodies, subjects, destination values, or secrets.
    """
    return batch_signer().sign_object(
        {"v": PREVIEW_TOKEN_VERSION, "fingerprint": digest, "rows": bound}
    )


def stale_preview_error():
    return LaunchError("Preview this cohort before confirming the schedule.")


def verify_preview_token(value):
    """Return the authoritative (fingerprint, row records) bound by the token.

    Any missing, forged, or edited token is rejected: editable client maps are
    never trusted for membership, revisions, anchors, or proposed times.
    """
    if not isinstance(value, str) or not value or len(value) > 1024 * 1024:
        raise stale_preview_error()
    try:
        payload = batch_signer().unsign_object(value)
    except BadSignature as error:
        raise LaunchError(
            "This preview changed or is not authentic. Preview again, "
            "then confirm the new schedule."
        ) from error
    if not isinstance(payload, dict):
        raise stale_preview_error()
    if payload.get("v") != PREVIEW_TOKEN_VERSION:
        raise stale_preview_error()
    digest = payload.get("fingerprint")
    if not isinstance(digest, str) or len(digest) != 64:
        raise stale_preview_error()
    rows = payload.get("rows")
    if not isinstance(rows, list) or len(rows) > PREVIEW_ROWS_MAX:
        raise stale_preview_error()
    cleaned = []
    seen: set[str] = set()
    for record in rows:
        cleaned.append(clean_record(record, seen))
    cleaned.sort(key=lambda record: record[0])
    return digest, cleaned


def clean_record(record, seen):
    if not isinstance(record, list) or len(record) != 4:
        raise stale_preview_error()
    key, revision, anchor, due = record
    if (
        not isinstance(key, str)
        or not key
        or len(key) > GENERATED_KEY_MAX
        or "\x00" in key
        or key in seen
    ):
        raise stale_preview_error()
    seen.add(key)
    if revision is not None and (isinstance(revision, bool) or not isinstance(revision, int)):
        raise stale_preview_error()
    for moment in (anchor, due):
        if moment is not None and (
            not isinstance(moment, str) or len(moment) > MOMENT_MAX or "\x00" in moment
        ):
            raise stale_preview_error()
    return [key, revision, anchor, due]


def execute_batch(payload):
    """Confirmed batch: token gate, fingerprint gate, generate, approve+schedule."""
    digest, records = verify_preview_token(payload.get("preview_token"))
    cohort_id = payload.get("cohort_id")
    with transaction.atomic():
        # Canonical Course-first order: the cohort row is serialized through
        # load_row(lock=True) only AFTER its course (and the existing
        # assignment, if any) are fenced. No get_or_create anywhere here: a
        # missing assignment stays missing (course defaults apply) and a
        # missing plan is created inside generate() under the held Cohort lock.
        try:
            course_id = (
                Cohort.objects.filter(pk=cohort_id).values_list("course_id", flat=True).first()
            )
        except (ValueError, TypeError):
            course_id = None
        if course_id is None:
            raise LaunchError("Record not found.", code="not_found", status=404)
        serialize_rows(Course.objects.filter(pk=course_id))
        assignment = AnnouncementAssignment.objects.filter(course_id=course_id).first()
        if assignment is not None:
            serialize_rows(AnnouncementAssignment.objects.filter(pk=assignment.pk))
        cohort = load_row(Cohort, cohort_id, lock=True)
        plan = CohortAnnouncementPlan.objects.filter(cohort_id=cohort.pk).first()
        if plan is not None:
            serialize_rows(CohortAnnouncementPlan.objects.filter(pk=plan.pk))
        check_fingerprint(cohort, digest)
        live_cohort(cohort)
        generate(cohort)
        return schedule_confirmed(cohort, records)


def check_fingerprint(cohort, preview_fingerprint):
    selection = effective_selection(cohort)
    digest = fingerprint_digest(fingerprint_value(cohort, selection))
    if digest != preview_fingerprint:
        raise LaunchError(
            "The cohort configuration changed since preview. Preview again, "
            "then confirm the new schedule.",
            code="preview_stale",
            status=409,
        )


def schedule_confirmed(cohort, records):
    now = timezone.now()
    by_key = {record[0]: record for record in records}
    enqueued = []
    skipped = []
    matched = set()
    ordered = list(
        Announcement.objects.filter(cohort=cohort, generated_key__in=list(by_key))
        .order_by("pk")
        .values_list("pk", "generated_key")
    )
    for pk, key in ordered:
        matched.add(key)
        kind, entry = schedule_key(cohort, key, by_key[key], now, pk)
        if kind == "enqueued":
            enqueued.append(entry)
        else:
            skipped.append(entry)
    for key in sorted(by_key):
        if key not in matched:
            skipped.append({"generated_key": key, "reason": "stale_revision"})
    for entry in report_unseen(cohort, set(by_key)):
        skipped.append(entry)
    ordered_enqueued = sorted(enqueued, key=lambda item: item["generated_key"])
    ordered_skipped = sorted(skipped, key=lambda item: item["generated_key"])
    return {
        "cohort_id": str(cohort.pk),
        "action": "launch_and_schedule",
        "enqueued": ordered_enqueued,
        "skipped": ordered_skipped,
        "enqueued_count": len(ordered_enqueued),
        "skipped_count": len(ordered_skipped),
    }


def schedule_key(cohort, key, expected, now, pk):
    locked = load_row(Announcement, pk, lock=True)
    if locked.cohort_id != cohort.pk or locked.generated_key != key:
        return ("skipped", {"generated_key": key, "reason": "stale_revision"})
    try:
        timing_start = timing_owner(locked.target_kind, locked.target_id).starts_at
    except LaunchError:
        return ("skipped", {"generated_key": key, "reason": "start_changed"})
    reason = classify_row(locked, timing_start, now)
    if reason == "eligible":
        reason = check_expectations(locked, expected)
    if reason != "eligible":
        return ("skipped", {"generated_key": key, "reason": reason})
    if expected[1] is None:
        # Prospective key: only a fresh, untouched draft may ship. A concurrent
        # approval, cancellation, or copy edit since preview is an unseen
        # change: never act on it.
        if not is_fresh_prospective(locked):
            return ("skipped", {"generated_key": key, "reason": "stale_revision"})
    elif locked.status == "approved":
        if locked.approved_revision != locked.revision:
            return ("skipped", {"generated_key": key, "reason": "stale_revision"})
        return schedule_locked(locked, key)
    elif locked.status != "draft" or locked.approved_revision is not None:
        return ("skipped", {"generated_key": key, "reason": "stale_revision"})
    approved = approve_row({"id": str(locked.pk), "expected_revision": locked.revision})
    return schedule_locked(locked, key, approved)


def check_expectations(row, expected):
    if iso(row.anchor_at) != expected[2]:
        return "start_changed"
    due = proposed_at(row.anchor_at, row.version) if row.version is not None else None
    if iso(due) != expected[3]:
        return "start_changed"
    revision = expected[1]
    if revision is not None and row.revision != revision:
        return "stale_revision"
    return "eligible"


def is_fresh_prospective(row):
    """A prospective row ships only when generate() alone produced its content.

    generate() leaves a draft with no approval whose subject/body are byte
    identical to its immutable version and whose context is the no-override
    render. Anything else — an approval, a cancellation, an edited copy, or
    edited context overrides (which render into the sent copy) — is unseen
    since preview and never ships.
    """
    if row.status != "draft" or row.approved_revision is not None:
        return False
    version = row.version
    if version is None:
        return False
    if row.subject != version.subject or row.body != version.body:
        return False
    if (row.context_overrides or {}) != {}:
        return False
    try:
        fresh_context = announcement_context(row.target_kind, row.target_id, {})
    except LaunchError:
        return False
    return row.context == fresh_context


def schedule_locked(row, key, approved=None):
    due = proposed_at(row.anchor_at, row.version)
    if due is None:
        return ("skipped", {"generated_key": key, "reason": "missing_start"})
    revision = approved["revision"] if approved is not None else row.revision
    scheduled = schedule_announcement(
        {
            "id": str(row.pk),
            "expected_revision": revision,
            "scheduled_at": iso(due),
            "confirmed": True,
            "action": "schedule",
        }
    )
    return (
        "enqueued",
        {
            "generated_key": key,
            "announcement_id": scheduled["id"],
            "scheduled_at": scheduled["scheduled_at"],
            "revision": scheduled["revision"],
        },
    )


def report_unseen(cohort, confirmed_keys):
    unseen = []
    for row in Announcement.objects.filter(cohort=cohort, generated_key__isnull=False).order_by(
        "pk"
    ):
        if row.generated_key in confirmed_keys:
            continue
        unseen.append({"generated_key": row.generated_key, "reason": "not_in_preview"})
    return unseen
