"""Import one cohort's historical homework and project scores.

Reads the graded ("processed") exports located by ``editions.py`` -- never
the ``raw/`` exports, which still carry GitHub links and free-text feedback
alongside plaintext email. The email itself is recovered separately, once
per cohort, by ``email_recovery.py`` (which does read ``raw/``, since that is
the only place most learners' real address survives) and used only to pick
the right account for each learner -- see ``identity.py`` for what is and is
not stored from it. Real module titles/homework text, when a local course
repo checkout is available, come from ``homework_content.py``.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from django.utils import timezone

from courses.models import (
    Answer,
    Cohort,
    Homework,
    HomeworkState,
    Project,
    ProjectState,
    ProjectSubmission,
    Question,
    QuestionTypes,
    Submission,
)

from .editions import EditionSource, HomeworkSource, ProjectSource
from .email_recovery import build_hash_to_email_map
from .homework_content import TopicContent, load_homework_topics
from .identity import get_or_create_enrollment, get_or_create_learner

IMPORTED_NOTE = (
    "Imported from the archived DataTalksClub/zoomcamp-scoring history. "
    "Learner identity is anonymized; scores are as graded at the time."
)

_WEEK_SPACING = timedelta(days=7)
_PROJECT_SPACING = timedelta(days=21)


@dataclass(frozen=True, slots=True)
class EditionImportResult:
    cohort: Cohort
    homeworks: list[Homework]
    projects: list[Project]
    homework_submissions: int
    project_submissions: int


def _to_int(value) -> int:
    if value in (None, "", "nan"):
        return 0
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return 0


def _to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes"}


def _aware(day: date) -> datetime:
    return timezone.make_aware(datetime(day.year, day.month, day.day, 23, 59))


def ensure_cohort(edition: EditionSource) -> Cohort:
    start_date = date(edition.year, edition.start_month, 1)
    homework_span = len(edition.homeworks) * 7
    project_span = len(edition.projects) * 21
    end_date = start_date + timedelta(days=homework_span + project_span + 14)

    cohort, _ = Cohort.objects.update_or_create(
        slug=edition.cohort_slug,
        defaults={
            "title": f"{edition.course_title} {edition.year}",
            "description": (
                f"{edition.course_title}, {edition.year} cohort. {IMPORTED_NOTE}"
            ),
            "start_date": start_date,
            "end_date": end_date,
            "finished": True,
            "visible": True,
            "first_homework_scored": True,
        },
    )
    return cohort


def _load_answers_config(answers_json: Path | None) -> tuple[dict[str, str], dict[str, float]]:
    if answers_json is None:
        return {}, {}
    try:
        raw = json.loads(answers_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, {}
    mapping = raw.get("mapping", {})
    text_by_column = {value: key for key, value in mapping.items()}
    points_by_column = {
        entry["question"]: entry.get("points", 1)
        for entry in raw.get("answers", [])
        if "question" in entry
    }
    return text_by_column, points_by_column


def _read_rows(csv_path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    return fieldnames, rows


def _import_homework(
    cohort: Cohort,
    source: HomeworkSource,
    position: int,
    hash_to_email: dict[str, str],
    topic: TopicContent | None,
) -> tuple[Homework, int]:
    fieldnames, rows = _read_rows(source.results_csv)
    text_by_column, points_by_column = _load_answers_config(source.answers_json)
    question_columns = [name for name in fieldnames if name.startswith("question")]

    due_date = cohort.start_date + position * _WEEK_SPACING
    title = topic.title if topic else f"Homework {source.slug_part}"
    instructions_markdown = topic.instructions_markdown if topic else ""
    homework, _ = Homework.objects.update_or_create(
        course=cohort,
        slug=f"homework-{source.slug_part}",
        defaults={
            "title": title,
            "description": IMPORTED_NOTE,
            "instructions_markdown": instructions_markdown,
            "due_date": _aware(due_date),
            "state": HomeworkState.SCORED.value,
        },
    )

    questions = {}
    for column in question_columns:
        points = points_by_column.get(column, 1) or 1
        text = text_by_column.get(column, column.replace("question", "Question "))
        question, _ = Question.objects.update_or_create(
            homework=homework,
            text=text,
            defaults={
                "question_type": QuestionTypes.FREE_FORM.value,
                "scores_for_correct_answer": int(points),
            },
        )
        questions[column] = (question, points)

    submission_count = 0
    for row in rows:
        source_key = row.get("email", "").strip()
        if not source_key:
            continue
        user, _ = get_or_create_learner(source_key, hash_to_email.get(source_key))
        enrollment, _ = get_or_create_enrollment(user, cohort)

        per_question_scores = {
            column: _to_int(row.get(column)) for column in question_columns
        }
        questions_score = sum(per_question_scores.values())
        lip_score = _to_int(row.get("learning_in_public"))
        faq_score = _to_int(row.get("faq_score"))
        total_score = _to_int(row.get("total_score", questions_score + lip_score + faq_score))

        submission, _ = Submission.objects.update_or_create(
            homework=homework,
            enrollment=enrollment,
            defaults={
                "student": user,
                "problems_comments": "",
                "questions_score": questions_score,
                "faq_score": faq_score,
                "learning_in_public_score": lip_score,
                "total_score": total_score,
            },
        )
        submission_count += 1

        Answer.objects.filter(submission=submission).delete()
        Answer.objects.bulk_create(
            Answer(
                submission=submission,
                question=question,
                answer_text=str(per_question_scores[column]),
                is_correct=per_question_scores[column] >= points,
            )
            for column, (question, points) in questions.items()
        )

    return homework, submission_count


def _load_assignment_lookup(assignment_csv: Path | None) -> dict[str, dict[str, str]]:
    if assignment_csv is None:
        return {}
    _fieldnames, rows = _read_rows(assignment_csv)
    lookup = {}
    for row in rows:
        key = (row.get("project_hash") or "").strip()
        if not key:
            continue
        lookup[key] = {
            "github_link": (row.get("project_url") or "").strip(),
            "commit_id": (row.get("commit") or "").strip(),
        }
    return lookup


_FALLBACK_GITHUB_LINK = "https://github.com/DataTalksClub/zoomcamp-scoring"


def _import_project(
    cohort: Cohort,
    source: ProjectSource,
    position: int,
    hash_to_email: dict[str, str],
) -> tuple[Project, int]:
    _fieldnames, rows = _read_rows(source.results_csv)
    assignment_lookup = _load_assignment_lookup(source.assignment_csv)

    due_date = cohort.start_date + timedelta(days=60) + position * _PROJECT_SPACING

    project, _ = Project.objects.update_or_create(
        course=cohort,
        slug=f"project-{source.slug_part}",
        defaults={
            "title": source.title,
            "description": IMPORTED_NOTE,
            "submission_due_date": _aware(due_date),
            "peer_review_due_date": _aware(due_date + timedelta(days=7)),
            "state": ProjectState.COMPLETED.value,
        },
    )

    submission_count = 0
    for row in rows:
        source_key = row.get("email", "").strip()
        if not source_key:
            continue
        user, _ = get_or_create_learner(source_key, hash_to_email.get(source_key))
        enrollment, _ = get_or_create_enrollment(user, cohort)

        link_info = assignment_lookup.get(source_key, {})
        github_link = link_info.get("github_link") or _FALLBACK_GITHUB_LINK
        commit_id = (link_info.get("commit_id") or "0000000")[:40]

        project_score = _to_int(row.get("project_total"))
        project_lip_score = _to_int(row.get("learning_in_public_project_score"))
        peer_review_score = _to_int(row.get("evaluation_score"))
        peer_review_lip_score = _to_int(row.get("learning_in_public_evaluation_score"))
        total_score = _to_int(
            row.get(
                "total_score",
                project_score + project_lip_score + peer_review_score + peer_review_lip_score,
            )
        )

        ProjectSubmission.objects.update_or_create(
            project=project,
            enrollment=enrollment,
            defaults={
                "student": user,
                "github_link": github_link,
                "commit_id": commit_id,
                "problems_comments": "",
                "project_score": project_score,
                "project_learning_in_public_score": project_lip_score,
                "peer_review_score": peer_review_score,
                "peer_review_learning_in_public_score": peer_review_lip_score,
                "total_score": total_score,
                "reviewed_enough_peers": _to_bool(row.get("evaluated_3_projects")),
                "passed": _to_bool(row.get("project_passed")),
            },
        )
        submission_count += 1

    return project, submission_count


def import_edition_scoring(edition: EditionSource, course_repos_dir: Path | None = None) -> EditionImportResult:
    cohort = ensure_cohort(edition)
    hash_to_email = build_hash_to_email_map(edition.email_source_csvs)
    topics = (
        load_homework_topics(course_repos_dir, edition.course_slug, edition.year, edition.homeworks)
        if course_repos_dir is not None
        else {}
    )

    homeworks = []
    homework_submissions = 0
    for position, source in enumerate(edition.homeworks):
        homework, count = _import_homework(
            cohort, source, position, hash_to_email, topics.get(source.slug_part)
        )
        homeworks.append(homework)
        homework_submissions += count

    projects = []
    project_submissions = 0
    for position, source in enumerate(edition.projects):
        project, count = _import_project(cohort, source, position, hash_to_email)
        projects.append(project)
        project_submissions += count

    return EditionImportResult(
        cohort=cohort,
        homeworks=homeworks,
        projects=projects,
        homework_submissions=homework_submissions,
        project_submissions=project_submissions,
    )


__all__ = [
    "EditionImportResult",
    "ensure_cohort",
    "import_edition_scoring",
]
