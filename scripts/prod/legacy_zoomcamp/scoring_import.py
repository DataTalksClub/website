"""Import one cohort's historical homework and project scores.

Reads the graded ("processed") exports located by ``editions.py`` for scores
-- never the ``raw/`` exports for those, which still carry GitHub links and
free-text feedback alongside plaintext email. The email itself is recovered
separately, once per cohort, by ``email_recovery.py`` (which does read
``raw/``, since that is the only place most learners' real address survives)
and used only to pick the right account for each learner -- see
``identity.py`` for what is and is not stored from it. Real module
titles/homework text, when a local course repo checkout is available, come
from ``homework_content.py``.

One field is the exception: ``Submission.submitted_at``. The graded exports
never carried a submission time at all, so this module also reads each
homework's own raw weekly export (``HomeworkSource.raw_csv``) for its real,
plaintext ``Timestamp`` column, hashes the email the same way the graded
export's key already is, and joins the two by that hash -- see
``_raw_email_to_timestamp``. No email or timestamp value from that read is
ever persisted; only the resulting datetime is.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from django.db import transaction
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
from .identity import get_or_create_enrollment, get_or_create_learner, sha1_hex

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
    # How many of ``homework_submissions`` got their real, historical
    # ``submitted_at`` from the raw weekly export vs. how many could not be
    # matched there (email absent from that week's raw export, or the export
    # itself missing) and so fell back to import time -- see
    # ``_raw_email_to_timestamp``. Documented, not silent: this must stay
    # visibly rare, not the common case.
    submissions_with_recovered_timestamp: int
    submissions_with_fallback_timestamp: int


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


def _cohort_start(cohort: Cohort) -> date:
    """``ensure_cohort`` always sets a start date; every due date is relative to it."""

    if cohort.start_date is None:
        raise ValueError("historical cohort has no start date")
    return cohort.start_date


def ensure_cohort(edition: EditionSource) -> Cohort:
    start_date = date(edition.year, edition.start_month, 1)
    homework_span = len(edition.homeworks) * 7
    project_span = len(edition.projects) * 21
    end_date = start_date + timedelta(days=homework_span + project_span + 14)

    cohort, _ = Cohort.objects.update_or_create(
        slug=edition.cohort_slug,
        defaults={
            "title": f"{edition.course_title} {edition.year}",
            "description": (f"{edition.course_title}, {edition.year} cohort. {IMPORTED_NOTE}"),
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


_EMAIL_COLUMN_HINT = "email"


def _find_email_column(fieldnames: list[str]) -> str | None:
    for name in fieldnames:
        if _EMAIL_COLUMN_HINT in name.lower():
            return name
    return None


def _sniff_date_order(date_parts: list[tuple[str, str, str]]) -> str:
    """Pick a field order for one raw export's ``Timestamp`` column.

    Google Forms bakes each week's ``Timestamp`` in whatever locale the
    export used at the time -- separator and field order vary export to
    export (not row to row within one export), confirmed by scanning every
    raw CSV this importer reads: ``YYYY/MM/DD``, ``DD/MM/YYYY`` and
    ``DD.MM.YYYY`` all occur, and so does the occasional US-locale
    ``MM/DD/YYYY``. A four-digit first field is unambiguous
    (year-month-day). Otherwise, any value over 12 in a field pins that
    field as the day, which pins the order for the whole export. A field
    order that never carries a value over 12 anywhere in the export is
    genuinely ambiguous from the data alone; day-first is this dataset's
    dominant format (confirmed against every raw export the five affected
    cohorts ship), so an ambiguous export defaults to it rather than being
    treated as unparseable.
    """

    saw_first_over_12 = False
    saw_second_over_12 = False
    for first, second, _third in date_parts:
        if len(first) == 4:
            return "ymd"
        try:
            first_value, second_value = int(first), int(second)
        except ValueError:
            continue
        if first_value > 12:
            saw_first_over_12 = True
        if second_value > 12:
            saw_second_over_12 = True
    if saw_first_over_12:
        return "dmy"
    if saw_second_over_12:
        return "mdy"
    return "dmy"


def _split_date_part(date_part: str) -> tuple[str, str, str] | None:
    separator = "/" if "/" in date_part else "." if "." in date_part else None
    if separator is None:
        return None
    parts = date_part.split(separator)
    if len(parts) != 3:
        return None
    return parts[0], parts[1], parts[2]


def _parse_raw_timestamp(value: str, date_order: str) -> datetime | None:
    """Parse one raw export's ``Timestamp`` cell using a pre-sniffed field order.

    Naive -- Google Forms never records which timezone a submission's
    ``Timestamp`` used, and nothing else in the export says either -- so the
    result is anchored to UTC. That is a documented approximation, not a
    recovered fact: it can be off by the exporting form-owner's real
    timezone offset, but it is still the learner's real historical week,
    which is the entire point of this join (audit: cohort dashboards were
    showing 100% "after the deadline" with every submission at import time
    before this).
    """

    date_part, _, time_part = value.strip().partition(" ")
    parts = _split_date_part(date_part)
    if parts is None:
        return None
    first, second, third = parts
    try:
        if date_order == "ymd":
            year, month, day = int(first), int(second), int(third)
        elif date_order == "mdy":
            month, day, year = int(first), int(second), int(third)
        else:
            day, month, year = int(first), int(second), int(third)
        time_fields = [int(field) for field in time_part.split(":")] if time_part else []
        time_fields += [0] * (3 - len(time_fields))
        hour, minute, second_value = time_fields[:3]
        return datetime(year, month, day, hour, minute, second_value, tzinfo=UTC)
    except (ValueError, IndexError):
        return None


def _raw_email_to_timestamp(raw_csv: Path | None) -> dict[str, datetime]:
    """Map a raw weekly export's real emails (hashed the upstream way) to
    the real ``Timestamp`` each learner submitted at.

    On a duplicate email within one export (a resubmission), the last row
    wins -- the same rule the upstream grading pipeline itself uses
    (``evaluate_week.py``: ``drop_duplicates(subset=['email'], keep='last')``),
    so this joins the timestamp of the submission that was actually graded,
    not an earlier draft.
    """

    if raw_csv is None or not raw_csv.exists():
        return {}
    fieldnames, rows = _read_rows(raw_csv)
    email_column = _find_email_column(fieldnames)
    if email_column is None:
        return {}

    date_parts = []
    for row in rows:
        date_part = (row.get("Timestamp") or "").strip().partition(" ")[0]
        parts = _split_date_part(date_part)
        if parts is not None:
            date_parts.append(parts)
    date_order = _sniff_date_order(date_parts)

    mapping: dict[str, datetime] = {}
    for row in rows:
        email = (row.get(email_column) or "").strip()
        timestamp_raw = (row.get("Timestamp") or "").strip()
        if not email or not timestamp_raw:
            continue
        parsed = _parse_raw_timestamp(timestamp_raw, date_order)
        if parsed is None:
            continue
        mapping[sha1_hex(email)] = parsed
    return mapping


def _import_homework(
    cohort: Cohort,
    source: HomeworkSource,
    position: int,
    hash_to_email: dict[str, str],
    topic: TopicContent | None,
) -> tuple[Homework, int, int, int]:
    fieldnames, rows = _read_rows(source.results_csv)
    text_by_column, points_by_column = _load_answers_config(source.answers_json)
    question_columns = [name for name in fieldnames if name.startswith("question")]
    raw_timestamps = _raw_email_to_timestamp(source.raw_csv)
    recovered_timestamp_count = 0
    fallback_timestamp_count = 0

    due_date = _cohort_start(cohort) + position * _WEEK_SPACING
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

        # Parsed and validated before any write, so a malformed row can never
        # leave a half-applied one behind.
        per_question_scores = {column: _to_int(row.get(column)) for column in question_columns}
        questions_score = sum(per_question_scores.values())
        lip_score = _to_int(row.get("learning_in_public"))
        faq_score = _to_int(row.get("faq_score"))
        total_score = _to_int(row.get("total_score", questions_score + lip_score + faq_score))

        # The learner association, the submission update, and the answer
        # replacement are one unit.  A process failure or database error
        # between the answer deletion and the re-insertion used to leave a
        # previously populated submission with no answers and moved score
        # totals (audit REL-16).  The transaction is bounded to this one
        # submission -- deliberately not one transaction for the whole
        # historical migration -- so a failed row leaves every earlier row
        # committed and a re-run repairs only what is missing.
        with transaction.atomic():
            user, _ = get_or_create_learner(source_key, hash_to_email.get(source_key))
            enrollment, _ = get_or_create_enrollment(user, cohort)

            submission_defaults = {
                "student": user,
                "problems_comments": "",
                "questions_score": questions_score,
                "faq_score": faq_score,
                "learning_in_public_score": lip_score,
                "total_score": total_score,
            }
            # ``source_key`` is already the same sha1(email) the raw export's
            # real address hashes to (see ``_raw_email_to_timestamp``), so no
            # extra pass through ``hash_to_email`` is needed here -- a direct
            # hit means this learner's real weekly ``Timestamp`` was
            # recovered; a miss (email absent from that week's raw export, or
            # no raw export at all for this homework) leaves ``submitted_at``
            # out of ``defaults`` entirely, so it only ever falls back to the
            # model's import-time default on first creation and a later
            # re-run never clobbers a real value with "now".
            recovered_at = raw_timestamps.get(source_key)
            if recovered_at is not None:
                submission_defaults["submitted_at"] = recovered_at
                recovered_timestamp_count += 1
            else:
                fallback_timestamp_count += 1

            submission, _ = Submission.objects.update_or_create(
                homework=homework,
                enrollment=enrollment,
                defaults=submission_defaults,
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

    return homework, submission_count, recovered_timestamp_count, fallback_timestamp_count


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

    due_date = _cohort_start(cohort) + timedelta(days=60) + position * _PROJECT_SPACING

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


def import_edition_scoring(
    edition: EditionSource, course_repos_dir: Path | None = None
) -> EditionImportResult:
    cohort = ensure_cohort(edition)
    hash_to_email = build_hash_to_email_map(edition.email_source_csvs)
    topics = (
        load_homework_topics(course_repos_dir, edition.course_slug, edition.year, edition.homeworks)
        if course_repos_dir is not None
        else {}
    )

    homeworks = []
    homework_submissions = 0
    recovered_timestamps = 0
    fallback_timestamps = 0
    for position, source in enumerate(edition.homeworks):
        homework, count, recovered, fallback = _import_homework(
            cohort, source, position, hash_to_email, topics.get(source.slug_part)
        )
        homeworks.append(homework)
        homework_submissions += count
        recovered_timestamps += recovered
        fallback_timestamps += fallback

    projects = []
    project_submissions = 0
    for position, project_source in enumerate(edition.projects):
        project, count = _import_project(cohort, project_source, position, hash_to_email)
        projects.append(project)
        project_submissions += count

    return EditionImportResult(
        cohort=cohort,
        homeworks=homeworks,
        projects=projects,
        homework_submissions=homework_submissions,
        project_submissions=project_submissions,
        submissions_with_recovered_timestamp=recovered_timestamps,
        submissions_with_fallback_timestamp=fallback_timestamps,
    )


__all__ = [
    "EditionImportResult",
    "ensure_cohort",
    "import_edition_scoring",
]
