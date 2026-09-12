import math
from datetime import timedelta

from django.db.models import Count
from django.db.models.functions import TruncWeek

from courses.models.homework import Submission

# A submission recorded well outside the cohort's own dates is an import or
# clock artefact, not real activity (ml-zoomcamp/2021 carries a submission
# timestamped years after the cohort's end_date). The grace window is wide
# enough to keep late real activity -- resubmissions, stragglers -- on the
# axis while dropping the isolated outlier from stretching it.
OUTLIER_GRACE_DAYS = 120
OUTLIER_LEAD_DAYS = 14

CHART_WIDTH = 720
CHART_HEIGHT = 260
CHART_PAD_LEFT = 46
CHART_PAD_RIGHT = 8
CHART_PAD_TOP = 22
CHART_PAD_BOTTOM = 34


def dashboard_engagement_trend(course):
    """Homework submissions per week, on a real time axis.

    Two corrections over a plain group-by: a week with nothing submitted is
    filled in as a zero-height bar instead of vanishing (so a four-week gap
    reads as a gap, not the same spacing as a one-week gap), and a submission
    recorded far outside the cohort's own dates is dropped from the axis
    rather than stretching it across years.

    Returns ``(weeks, dropped_count)``. ``dropped_count`` is the number of
    submissions excluded as out-of-range, for an optional footnote.
    """

    weekly = (
        Submission.objects
        .filter(homework__course=course, submitted_at__isnull=False)
        .annotate(week=TruncWeek("submitted_at"))
        .values("week")
        .annotate(count=Count("id"))
        .order_by("week")
    )

    rows = [
        {"week": row["week"].date(), "count": row["count"]}
        for row in weekly
        if row["week"] is not None
    ]
    if not rows:
        return [], 0

    kept_rows, dropped_count = _drop_out_of_range_weeks(course, rows)
    filled_rows = _fill_weekly_gaps(kept_rows)
    max_count = max(row["count"] for row in filled_rows) or 1

    weeks = [
        {
            "date": row["week"],
            "label": row["week"].strftime("%b %d, %Y"),
            "short_label": _short_week_label(row["week"]),
            "count": row["count"],
            "bar_pct": round(row["count"] / max_count * 100, 1),
        }
        for row in filled_rows
    ]
    return weeks, dropped_count


def _drop_out_of_range_weeks(course, rows):
    start = course.start_date
    end = course.end_date
    if not start or not end:
        return rows, 0

    window_start = start - timedelta(days=OUTLIER_LEAD_DAYS)
    window_end = end + timedelta(days=OUTLIER_GRACE_DAYS)
    kept = []
    dropped_count = 0
    for row in rows:
        if window_start <= row["week"] <= window_end:
            kept.append(row)
        else:
            dropped_count += row["count"]

    if not kept:
        # Every observed week fell outside the cohort's own dates: an
        # over-eager filter here would show nothing at all, which is a worse
        # failure than an unfiltered chart, so fall back to the raw rows.
        return rows, 0
    return kept, dropped_count


def _fill_weekly_gaps(rows):
    ordered = sorted(rows, key=lambda row: row["week"])
    by_week = {row["week"]: row["count"] for row in ordered}
    current = ordered[0]["week"]
    last = ordered[-1]["week"]
    filled = []
    while current <= last:
        filled.append({"week": current, "count": by_week.get(current, 0)})
        current += timedelta(days=7)
    return filled


def _short_week_label(week_date):
    # "%-d" (no leading zero) is a glibc extension; every environment this
    # runs in is Linux, so it is safe here.
    return week_date.strftime("%b %-d")


def dashboard_engagement_chart(weeks):
    """Server-rendered SVG geometry for the weekly column chart.

    One geometry serves both the desktop and phone layout: the page hides
    every label that is not a multiple-of-N week under a narrow media query
    rather than redrawing the axis, so a phone reader still sees a real time
    axis rather than a squeezed one.
    """

    if not weeks:
        return None

    counts = [week["count"] for week in weeks]
    max_count = max(counts)
    axis_max, step = _nice_axis(max_count)

    plot_width = CHART_WIDTH - CHART_PAD_LEFT - CHART_PAD_RIGHT
    plot_height = CHART_HEIGHT - CHART_PAD_TOP - CHART_PAD_BOTTOM
    week_count = len(weeks)
    slot = plot_width / week_count
    gap = 2 if slot > 6 else 0
    bar_width = max(slot - gap, 1)
    peak_index = max(range(week_count), key=lambda i: counts[i])

    def y_for(value):
        if axis_max <= 0:
            return CHART_PAD_TOP + plot_height
        return CHART_PAD_TOP + plot_height - (value / axis_max * plot_height)

    # Every fourth week gets an axis label on a phone; the rest fold away
    # under a narrow media query so the labels that remain stay legible
    # rather than shrinking to fit them all.
    narrow_label_stride = max(1, math.ceil(week_count / 8))

    bars = []
    for index, week in enumerate(weeks):
        top = y_for(week["count"])
        bars.append(
            {
                "x": round(CHART_PAD_LEFT + slot * index + gap / 2, 2),
                "y": round(top, 2),
                "width": round(bar_width, 2),
                "height": round((CHART_PAD_TOP + plot_height) - top, 2),
                "hit_x": round(CHART_PAD_LEFT + slot * index, 2),
                "hit_width": round(slot, 2),
                "label_x": round(CHART_PAD_LEFT + slot * index + slot / 2, 2),
                "short_label": week["short_label"],
                "full_label": week["label"],
                "count": week["count"],
                "is_peak": index == peak_index,
                "show_label_narrow": index % narrow_label_stride == 0,
            }
        )

    gridlines = []
    value = 0
    while value <= axis_max:
        gridlines.append({"y": round(y_for(value), 2), "value": value})
        value += step

    peak = bars[peak_index]
    axis_label = (
        f"Homework submissions per week, {weeks[0]['label']} to "
        f"{weeks[-1]['label']}, peaking at {counts[peak_index]:,} in the week "
        f"of {weeks[peak_index]['label']}"
    )

    return {
        "width": CHART_WIDTH,
        "height": CHART_HEIGHT,
        "baseline_y": round(y_for(0), 2),
        "pad_left": CHART_PAD_LEFT,
        "bars": bars,
        "gridlines": gridlines,
        "peak": peak,
        "axis_label": axis_label,
    }


def _nice_axis(max_value):
    """A gridline step that lands on a round number, with 3-5 steps shown."""

    if max_value <= 0:
        return 1, 1
    rough_step = max_value / 4
    magnitude = 10 ** math.floor(math.log10(rough_step)) if rough_step >= 1 else 1
    for multiple in (1, 2, 5, 10):
        step = magnitude * multiple
        axis_max = step
        while axis_max < max_value:
            axis_max += step
        if step * 6 >= max_value:
            return axis_max, step
    step = magnitude * 10
    axis_max = step
    while axis_max < max_value:
        axis_max += step
    return axis_max, step
