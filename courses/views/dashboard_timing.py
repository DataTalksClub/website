from courses.models.homework import Submission


# Buckets of how early a submission arrived, keyed by a lower bound in days
# before the due date. Ordered from earliest to latest.
TIMING_BUCKETS = (
    (7.0, "A week or more early"),
    (3.0, "3-7 days early"),
    (1.0, "1-3 days early"),
    (0.0, "Final day"),
    (float("-inf"), "After the deadline"),
)


def dashboard_submission_timing(course):
    submissions = (
        Submission.objects
        .filter(homework__course=course)
        .values_list("submitted_at", "homework__due_date")
    )

    counts = {label: 0 for _, label in TIMING_BUCKETS}
    total = 0
    for submitted_at, due_date in submissions:
        if submitted_at is None or due_date is None:
            continue
        days_before = (due_date - submitted_at).total_seconds() / 86400
        counts[timing_bucket_label(days_before)] += 1
        total += 1

    if total == 0:
        return []

    return [
        {
            "label": label,
            "count": counts[label],
            "pct": round(counts[label] / total * 100, 1),
            # 1-indexed position in the fixed bucket order, so the template
            # can pick the page-local ordinal ramp colour without re-deriving
            # "which bucket is this" from the label text.
            "rank": rank,
        }
        for rank, (_, label) in enumerate(TIMING_BUCKETS, start=1)
    ]


def timing_bucket_label(days_before):
    for lower_bound, label in TIMING_BUCKETS:
        if days_before >= lower_bound:
            return label
    return TIMING_BUCKETS[-1][1]


# The label every bucket list ends on -- kept as a constant rather than
# re-spelled at each call site, since the buckets always end with it.
LATE_BUCKET_LABEL = TIMING_BUCKETS[-1][1]


def dashboard_submission_timing_is_degenerate(buckets):
    """Whether every submission landed in a single bucket.

    A single 100%-wide bucket is an import artefact (every timestamp on
    ml-zoomcamp/2021 was recorded after its deadline), not a real timing
    distribution -- the caller replaces the chart with a note instead of
    drawing four empty tracks and one full one.
    """

    return any(bucket["pct"] >= 100.0 for bucket in buckets)


def dashboard_submission_timing_deadline_pct(buckets):
    """Share of submissions that arrived before the deadline.

    This is where the stacked bar's deadline tick sits: the complement of
    the trailing "After the deadline" bucket, which is always last.
    """

    if not buckets:
        return None
    late_pct = buckets[-1]["pct"]
    return round(100 - late_pct, 1)
