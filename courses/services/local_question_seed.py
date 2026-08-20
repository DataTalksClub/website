"""Fail-closed local-development seed for representative homework questions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.db import transaction

from core.bootstrap import RuntimeEnvironment
from courses.models import (
    QUESTION_ANSWER_DELIMITER,
    AnswerTypes,
    Cohort,
    Homework,
    Question,
    QuestionTypes,
)

DEFAULT_COHORT_SLUG = "de-zoomcamp-2026"
DEFAULT_HOMEWORK_SLUG = "homework-01-homework-1-docker-sql-and-terraform"

ALLOWED_ENVIRONMENTS = frozenset({RuntimeEnvironment.LOCAL, RuntimeEnvironment.TEST})
SQLITE_ENGINE = "django.db.backends.sqlite3"

CHOICE_QUESTION_TYPES = frozenset(
    {
        QuestionTypes.MULTIPLE_CHOICE.value,
        QuestionTypes.CHECKBOXES.value,
    }
)
FREE_FORM_QUESTION_TYPES = frozenset(
    {
        QuestionTypes.FREE_FORM.value,
        QuestionTypes.FREE_FORM_LONG.value,
    }
)
VALID_ANSWER_TYPES = frozenset(answer_type.value for answer_type in AnswerTypes)


class LocalQuestionSeedError(RuntimeError):
    """A fail-closed refusal: wrong environment, target, or question spec."""


@dataclass(frozen=True, slots=True)
class LocalQuestionSpec:
    """One stable question definition owned by this local seed."""

    text: str
    question_type: str
    possible_answers: tuple[str, ...] = ()
    correct_answer: str = ""
    answer_type: str | None = None
    scores_for_correct_answer: int = 1


@dataclass(frozen=True, slots=True)
class SeededQuestion:
    """One seeded question and whether this run created its row."""

    text: str
    question_type: str
    created: bool


@dataclass(frozen=True, slots=True)
class LocalQuestionSeedResult:
    """Everything the command needs to report without another database read."""

    cohort_slug: str
    homework_slug: str
    questions: tuple[SeededQuestion, ...]

    @property
    def question_count(self) -> int:
        return len(self.questions)

    @property
    def questions_created(self) -> int:
        return sum(question.created for question in self.questions)

    def summary(self) -> dict[str, Any]:
        return {
            "cohort_slug": self.cohort_slug,
            "homework_slug": self.homework_slug,
            "questions": self.question_count,
            "questions_created": self.questions_created,
            "question_types": [question.question_type for question in self.questions],
        }


LOCAL_QUESTION_SPECS: tuple[LocalQuestionSpec, ...] = (
    LocalQuestionSpec(
        text="Which tool runs SQL commands against a PostgreSQL database?",
        question_type=QuestionTypes.MULTIPLE_CHOICE.value,
        possible_answers=("psql", "Terraform", "Docker Compose"),
        correct_answer="1",
    ),
    LocalQuestionSpec(
        text="Which tools are used in this homework's local data stack?",
        question_type=QuestionTypes.CHECKBOXES.value,
        possible_answers=("Docker", "PostgreSQL", "Terraform", "Kubernetes"),
        correct_answer="1,2,3",
    ),
    LocalQuestionSpec(
        text="What command-line client is used to connect to PostgreSQL?",
        question_type=QuestionTypes.FREE_FORM.value,
        correct_answer="psql",
        answer_type=AnswerTypes.EXACT_STRING.value,
    ),
)


def assert_local_database() -> None:
    """Refuse to touch anything but a local/test SQLite database."""

    environment = getattr(settings, "RUNTIME_ENVIRONMENT", None)
    if environment not in ALLOWED_ENVIRONMENTS:
        raise LocalQuestionSeedError("environment-not-local")
    engine = settings.DATABASES.get("default", {}).get("ENGINE")
    if engine != SQLITE_ENGINE:
        raise LocalQuestionSeedError("database-not-local-sqlite")


def _validate_question_spec(spec: LocalQuestionSpec) -> None:
    if not spec.text or spec.scores_for_correct_answer < 0:
        raise LocalQuestionSeedError("question-spec-invalid")

    if spec.question_type in CHOICE_QUESTION_TYPES:
        if (
            spec.answer_type is not None
            or len(spec.possible_answers) < 2
            or len(set(spec.possible_answers)) != len(spec.possible_answers)
            or any(not answer.strip() for answer in spec.possible_answers)
        ):
            raise LocalQuestionSeedError("question-spec-invalid")
        try:
            correct_indices = [int(index) for index in spec.correct_answer.split(",")]
        except ValueError as error:
            raise LocalQuestionSeedError("question-spec-invalid") from error
        if (
            not correct_indices
            or len(set(correct_indices)) != len(correct_indices)
            or any(index < 1 or index > len(spec.possible_answers) for index in correct_indices)
        ):
            raise LocalQuestionSeedError("question-spec-invalid")
        return

    if spec.question_type in FREE_FORM_QUESTION_TYPES:
        if (
            spec.possible_answers
            or not spec.correct_answer
            or spec.answer_type not in VALID_ANSWER_TYPES
        ):
            raise LocalQuestionSeedError("question-spec-invalid")
        return

    raise LocalQuestionSeedError("question-spec-invalid")


def validate_question_specs() -> None:
    """Validate every definition before a command can write any question."""

    if not LOCAL_QUESTION_SPECS:
        raise LocalQuestionSeedError("question-spec-invalid")
    for spec in LOCAL_QUESTION_SPECS:
        _validate_question_spec(spec)


def _selected_homework(cohort_slug: str, homework_slug: str) -> tuple[Cohort, Homework]:
    cohort = Cohort.objects.filter(slug=cohort_slug).first()
    if cohort is None:
        raise LocalQuestionSeedError("cohort-not-found")

    homework = Homework.objects.filter(course=cohort, slug=homework_slug).first()
    if homework is None:
        raise LocalQuestionSeedError("homework-not-found")
    return cohort, homework


def check_local_question_seed(*, cohort_slug: str, homework_slug: str) -> None:
    """Validate the environment, definitions, and selected homework without writing."""

    assert_local_database()
    validate_question_specs()
    _selected_homework(cohort_slug, homework_slug)


def _question_defaults(spec: LocalQuestionSpec) -> dict[str, Any]:
    possible_answers = (
        QUESTION_ANSWER_DELIMITER.join(spec.possible_answers) if spec.possible_answers else None
    )
    return {
        "question_type": spec.question_type,
        "answer_type": spec.answer_type,
        "possible_answers": possible_answers,
        "correct_answer": spec.correct_answer,
        "scores_for_correct_answer": spec.scores_for_correct_answer,
    }


def seed_local_questions(
    *,
    cohort_slug: str = DEFAULT_COHORT_SLUG,
    homework_slug: str = DEFAULT_HOMEWORK_SLUG,
) -> LocalQuestionSeedResult:
    """Create representative questions for one existing local homework."""

    assert_local_database()
    validate_question_specs()
    cohort, homework = _selected_homework(cohort_slug, homework_slug)

    with transaction.atomic():
        seeded = []
        for spec in LOCAL_QUESTION_SPECS:
            _question, created = Question.objects.update_or_create(
                homework=homework,
                text=spec.text,
                defaults=_question_defaults(spec),
            )
            seeded.append(
                SeededQuestion(
                    text=spec.text,
                    question_type=spec.question_type,
                    created=created,
                )
            )

    return LocalQuestionSeedResult(
        cohort_slug=cohort.slug,
        homework_slug=homework.slug,
        questions=tuple(seeded),
    )
