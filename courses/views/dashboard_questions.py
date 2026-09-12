from django.db.models import Count, Q

from courses.models.homework import Answer, AnswerTypes, HomeworkState


def dashboard_question_difficulty(course):
    """Per-question correctness, grouped by homework.

    Questions with answer_type ANY are excluded: they are graded on
    participation (any non-empty answer is "correct"), so their correctness
    rate is not a measure of difficulty.
    """
    rows = (
        Answer.objects
        .filter(question__homework__course=course)
        .filter(question__homework__state=HomeworkState.SCORED.value)
        .exclude(question__answer_type=AnswerTypes.ANY.value)
        .values(
            "question_id",
            "question__text",
            "question__homework_id",
            "question__homework__title",
        )
        .annotate(
            total=Count("id"),
            correct=Count("id", filter=Q(is_correct=True)),
        )
        .order_by("question__homework_id", "question_id")
    )

    groups = []
    group_by_homework = {}
    for row in rows:
        homework_id = row["question__homework_id"]
        group = group_by_homework.get(homework_id)
        if group is None:
            group = {
                "homework_id": homework_id,
                "homework_title": row["question__homework__title"],
                "questions": [],
            }
            group_by_homework[homework_id] = group
            groups.append(group)
        group["questions"].append(dashboard_question_row(row))

    for group in groups:
        # Hardest first inside a homework, so a reader scanning one group
        # meets its worst question first instead of hunting a flat table.
        group["questions"].sort(key=_question_sort_key)
        answered = [
            q["pct_correct"] for q in group["questions"]
            if q["pct_correct"] is not None
        ]
        group["lowest_pct_correct"] = min(answered) if answered else None
        group["question_count"] = len(group["questions"])
        group["answers_per_question"] = group["questions"][0]["total"]

    return groups


def _question_sort_key(question):
    pct_correct = question["pct_correct"]
    # Unanswered questions (no submissions yet) sort last rather than first,
    # since "no data" is not the same finding as "hard".
    return (pct_correct is None, pct_correct if pct_correct is not None else 0)


def dashboard_question_row(row):
    total = row["total"]
    correct = row["correct"]
    pct_correct = round(correct / total * 100, 1) if total else None
    return {
        "text": row["question__text"],
        "total": total,
        "correct": correct,
        "pct_correct": pct_correct,
    }


def dashboard_hardest_questions(question_difficulty, limit=3):
    """The N hardest questions across the whole cohort, for the lead cards.

    Ties and near-ties are common (several questions at the same low
    percentage), so this is a straight sort rather than a per-homework pick --
    the cards say "hardest", "second", "third" and mean it cohort-wide.
    """

    candidates = []
    for group in question_difficulty:
        for question in group["questions"]:
            if question["pct_correct"] is None:
                continue
            candidates.append({**question, "homework_title": group["homework_title"]})

    candidates.sort(key=lambda question: question["pct_correct"])
    return candidates[:limit]
