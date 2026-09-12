from collections import defaultdict
from dataclasses import dataclass

from django.db.models import Sum

from courses.models.homework import Homework, HomeworkState, Submission
from courses.views.dashboard_metrics import (
    quartile_fields,
    safe_quartiles,
    submission_values,
)


@dataclass(frozen=True)
class HomeworkScoreRatio:
    questions_score_median: object
    max_questions_score: object
    score_ratio: object


def dashboard_homeworks(course):
    homeworks = Homework.objects.filter(course=course)
    homeworks = homeworks.filter(state=HomeworkState.SCORED.value)
    homeworks = homeworks.order_by("id")
    max_questions_score = Sum("question__scores_for_correct_answer")
    return homeworks.annotate(
        max_questions_score=max_questions_score,
    )


def dashboard_homework_submissions(course):
    submission_fields = (
        "homework_id",
        "time_spent_lectures",
        "time_spent_homework",
        "questions_score",
        "total_score",
    )
    return (
        Submission.objects
        .filter(homework__course=course)
        .select_related("homework")
        .values(*submission_fields)
    )


def dashboard_homework_stats(
    homeworks, all_hw_submissions, total_enrollments
):
    hw_submissions_by_homework = dashboard_homework_submissions_by_homework(
        all_hw_submissions
    )
    homework_stats = dashboard_homework_stat_rows(
        homeworks,
        hw_submissions_by_homework,
        total_enrollments,
    )
    difficulty_stats = dashboard_homework_difficulty_stats(homework_stats)
    return homework_stats, difficulty_stats


def dashboard_homework_submissions_by_homework(all_hw_submissions):
    hw_submissions_by_homework = defaultdict(list)
    for submission in all_hw_submissions:
        homework_id = submission["homework_id"]
        hw_submissions_by_homework[homework_id].append(submission)
    return hw_submissions_by_homework


def dashboard_homework_stat_rows(
    homeworks,
    hw_submissions_by_homework,
    total_enrollments,
):
    stat_rows = []
    for homework in homeworks:
        submissions = hw_submissions_by_homework.get(homework.id, [])
        stat_row = dashboard_homework_stat(
            homework,
            submissions,
            total_enrollments,
        )
        stat_rows.append(stat_row)
    return stat_rows


def dashboard_homework_stat(homework, hw_submissions, total_enrollments):
    time_stats = dashboard_homework_time_stats(hw_submissions)
    score_stats = dashboard_homework_score_stats(homework, hw_submissions)
    submissions_count = len(hw_submissions)
    completion_rate = dashboard_completion_rate(
        submissions_count,
        total_enrollments,
    )

    return {
        "homework": homework,
        "submissions_count": submissions_count,
        "completion_rate": completion_rate,
        **time_stats,
        **score_stats,
    }


def dashboard_completion_rate(submissions_count, total_enrollments):
    if total_enrollments <= 0:
        return 0.0
    return round(submissions_count / total_enrollments * 100, 1)


def dashboard_homework_time_stats(hw_submissions):
    stats = {}
    lecture_times = submission_values(
        hw_submissions,
        "time_spent_lectures",
    )
    lecture_time_stats = quartile_fields("time_lecture", lecture_times)
    stats.update(lecture_time_stats)

    homework_times = submission_values(
        hw_submissions,
        "time_spent_homework",
    )
    homework_time_stats = quartile_fields("time_homework", homework_times)
    stats.update(homework_time_stats)

    total_times = dashboard_total_homework_times(hw_submissions)
    total_time_stats = quartile_fields("time_total", total_times)
    stats.update(total_time_stats)

    stats.update(
        dashboard_homework_time_split(
            lecture_time_stats["time_lecture_median"],
            homework_time_stats["time_homework_median"],
        )
    )
    return stats


def dashboard_homework_time_split(lecture_median, homework_median):
    """The lecture/homework share of the median total time, for the split bar.

    ``None`` when either half is missing rather than a misleading 100/0
    split -- a homework whose submissions never recorded lecture time still
    has a homework-time median, but there is nothing to split it against.
    """

    if lecture_median is None or homework_median is None:
        return {"time_lecture_pct": None, "time_homework_pct": None}
    total = lecture_median + homework_median
    if total <= 0:
        return {"time_lecture_pct": None, "time_homework_pct": None}
    lecture_pct = round(lecture_median / total * 100, 1)
    return {
        "time_lecture_pct": lecture_pct,
        "time_homework_pct": round(100 - lecture_pct, 1),
    }


def dashboard_total_homework_times(hw_submissions):
    total_times = []
    for submission in hw_submissions:
        lectures_time = submission["time_spent_lectures"]
        homework_time = submission["time_spent_homework"]
        if lectures_time is None or homework_time is None:
            continue
        total_time = lectures_time + homework_time
        total_times.append(total_time)
    return total_times


def dashboard_homework_score_stats(homework, hw_submissions):
    total_scores = submission_values(hw_submissions, "total_score")
    score_quartiles = safe_quartiles(total_scores)
    score_ratio_data = dashboard_homework_score_ratio(
        homework,
        hw_submissions,
    )
    score_ratio_pct = None
    if score_ratio_data.score_ratio is not None:
        score_ratio_pct = round(score_ratio_data.score_ratio * 100, 1)

    return {
        "score_q25": score_quartiles.q25,
        "score_median": score_quartiles.median,
        "score_q75": score_quartiles.q75,
        "questions_score_median": score_ratio_data.questions_score_median,
        "max_questions_score": score_ratio_data.max_questions_score,
        "score_ratio": score_ratio_data.score_ratio,
        "score_ratio_pct": score_ratio_pct,
        "score_dots": dashboard_score_dots(
            score_ratio_data.questions_score_median,
            score_ratio_data.max_questions_score,
        ),
    }


def dashboard_score_dots(questions_score_median, max_questions_score):
    """One dot per achievable point, filled up to the rounded median score.

    ``None`` when there is nothing to draw against (no scored submissions, or
    a homework with no scored questions) rather than a row of empty dots.
    """

    if not max_questions_score or questions_score_median is None:
        return None
    total_dots = int(round(max_questions_score))
    if total_dots <= 0:
        return None
    filled = max(0, min(total_dots, int(round(questions_score_median))))
    return {
        "filled": filled,
        "total": total_dots,
        "dots": [index < filled for index in range(total_dots)],
    }


def dashboard_homework_score_ratio(homework, hw_submissions):
    questions_scores = submission_values(hw_submissions, "questions_score")
    questions_score_quartiles = safe_quartiles(questions_scores)
    max_questions_score = homework.max_questions_score
    if questions_score_quartiles.median is not None and max_questions_score:
        score_ratio = questions_score_quartiles.median / max_questions_score
    else:
        score_ratio = None
    return HomeworkScoreRatio(
        questions_score_median=questions_score_quartiles.median,
        max_questions_score=max_questions_score,
        score_ratio=score_ratio,
    )


def _homework_difficulty_sort_key(hw_stat):
    score_ratio = hw_stat["score_ratio"]
    submissions_count = hw_stat["submissions_count"]
    homework_title = hw_stat["homework"].title
    return score_ratio, -submissions_count, homework_title


def dashboard_homework_difficulty_stats(homework_stats):
    difficulty_stats = []
    for hw_stat in homework_stats:
        hw_stat["is_hardest_tier"] = False
        homework = hw_stat["homework"]
        if (
            hw_stat["score_ratio"] is not None
            and homework.state == HomeworkState.SCORED.value
        ):
            difficulty_stats.append(hw_stat)
    difficulty_stats.sort(key=_homework_difficulty_sort_key)
    for rank, hw_stat in enumerate(difficulty_stats, start=1):
        hw_stat["difficulty_rank"] = rank

    # Ties are the common case (several homeworks land on the same lowest
    # median question score) -- a rank column would assert an order the data
    # does not contain, so every homework at the lowest score_ratio_pct
    # instead gets one shared "hardest tier" mark.
    if difficulty_stats:
        hardest_pct = difficulty_stats[0]["score_ratio_pct"]
        for hw_stat in difficulty_stats:
            hw_stat["is_hardest_tier"] = hw_stat["score_ratio_pct"] == hardest_pct

    return difficulty_stats
