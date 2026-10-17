"""PII-free audience and delivery summaries for operator previews."""

from collections import Counter

from django.db.models import Count

from courses.models import Enrollment


def eligible_count(row):
    if row.channel != "email":
        return 0
    cached = getattr(row, "launch_audience_count", None)
    if cached is not None:
        return cached
    return (
        Enrollment.objects.filter(course_id=row.cohort_id)
        .exclude(student__email_course_updates=False)
        .count()
    )


def annotate_audiences(rows):
    cohort_ids = [row.cohort_id for row in rows]
    values = (
        Enrollment.objects.filter(course_id__in=cohort_ids)
        .exclude(student__email_course_updates=False)
        .values("course_id")
        .annotate(total=Count("pk"))
    )
    counts = {}
    for value in values:
        counts[value["course_id"]] = value["total"]
    for row in rows:
        row.launch_audience_count = counts.get(row.cohort_id, 0)


def delivery_summary(row):
    states: Counter[str] = Counter()
    for delivery in row.deliveries.all():
        state = delivery.state
        if delivery.email_delivery_id:
            state = delivery.email_delivery.state
        states[state] += 1
    return dict(states)


def schedule_display(row):
    unarmed = row.status in {"draft", "approved", "cancelled", "blocked"}
    label = "Scheduled time"
    if unarmed:
        label = "Proposed time"
    return {"unarmed": unarmed, "time_label": label}
