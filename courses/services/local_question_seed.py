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
HOMEWORK_SLUGS = (
    "homework-01-homework-1-docker-sql-and-terraform",
    "homework-02-homework-2-workflow-orchestration",
    "homework-03-homework-3-data-warehousing",
    "homework-04-homework-4-analytics-engineering",
)
DEFAULT_HOMEWORK_SLUG = HOMEWORK_SLUGS[0]

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
    homework_slug: str | None = None


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
        homework_slug=HOMEWORK_SLUGS[0],
    ),
    LocalQuestionSpec(
        text="Which tools are used in this homework's local data stack?",
        question_type=QuestionTypes.CHECKBOXES.value,
        possible_answers=("Docker", "PostgreSQL", "Terraform", "Kubernetes"),
        correct_answer="1,2,3",
        homework_slug=HOMEWORK_SLUGS[0],
    ),
    LocalQuestionSpec(
        text="What command-line client is used to connect to PostgreSQL?",
        question_type=QuestionTypes.FREE_FORM.value,
        correct_answer="psql",
        answer_type=AnswerTypes.EXACT_STRING.value,
        homework_slug=HOMEWORK_SLUGS[0],
    ),
    LocalQuestionSpec(
        text="How many primary services does this local stack start?",
        question_type=QuestionTypes.FREE_FORM.value,
        correct_answer="3",
        answer_type=AnswerTypes.INTEGER.value,
        homework_slug=HOMEWORK_SLUGS[0],
    ),
    LocalQuestionSpec(
        text="Name the command used to start the local services.",
        question_type=QuestionTypes.FREE_FORM_LONG.value,
        correct_answer="docker compose up",
        answer_type=AnswerTypes.CONTAINS_STRING.value,
        homework_slug=HOMEWORK_SLUGS[0],
    ),
    LocalQuestionSpec(
        text="Which tool schedules the workflow for this homework?",
        question_type=QuestionTypes.MULTIPLE_CHOICE.value,
        possible_answers=("Airflow", "Docker", "Terraform", "PostgreSQL"),
        correct_answer="1",
        homework_slug=HOMEWORK_SLUGS[1],
    ),
    LocalQuestionSpec(
        text="Which items are part of a scheduled workflow?",
        question_type=QuestionTypes.CHECKBOXES.value,
        possible_answers=("Tasks", "A DAG", "A schedule", "CSS styles"),
        correct_answer="1,2,3",
        scores_for_correct_answer=2,
        homework_slug=HOMEWORK_SLUGS[1],
    ),
    LocalQuestionSpec(
        text="Which orchestration tool is used in this homework?",
        question_type=QuestionTypes.FREE_FORM.value,
        correct_answer="Airflow",
        answer_type=AnswerTypes.EXACT_STRING.value,
        homework_slug=HOMEWORK_SLUGS[1],
    ),
    LocalQuestionSpec(
        text="What is the example workflow's average task duration in minutes?",
        question_type=QuestionTypes.FREE_FORM.value,
        correct_answer="2.5",
        answer_type=AnswerTypes.FLOAT.value,
        homework_slug=HOMEWORK_SLUGS[1],
    ),
    LocalQuestionSpec(
        text="Which warehouse stores the analytics data for this homework?",
        question_type=QuestionTypes.MULTIPLE_CHOICE.value,
        possible_answers=("BigQuery", "Redis", "SQLite", "Nginx"),
        correct_answer="1",
        homework_slug=HOMEWORK_SLUGS[2],
    ),
    LocalQuestionSpec(
        text="Which capabilities are central to a data warehouse?",
        question_type=QuestionTypes.CHECKBOXES.value,
        possible_answers=(
            "SQL analytics",
            "Historical data",
            "Columnar storage",
            "Browser styling",
        ),
        correct_answer="1,2,3",
        scores_for_correct_answer=2,
        homework_slug=HOMEWORK_SLUGS[2],
    ),
    LocalQuestionSpec(
        text="How many source tables are used in the example warehouse?",
        question_type=QuestionTypes.FREE_FORM.value,
        correct_answer="3",
        answer_type=AnswerTypes.INTEGER.value,
        homework_slug=HOMEWORK_SLUGS[2],
    ),
    LocalQuestionSpec(
        text="Name the SQL operation used to combine related warehouse tables.",
        question_type=QuestionTypes.FREE_FORM_LONG.value,
        correct_answer="JOIN",
        answer_type=AnswerTypes.CONTAINS_STRING.value,
        homework_slug=HOMEWORK_SLUGS[2],
    ),
    LocalQuestionSpec(
        text="Which tool builds the analytics engineering models?",
        question_type=QuestionTypes.MULTIPLE_CHOICE.value,
        possible_answers=("dbt", "Docker", "Terraform", "Airflow"),
        correct_answer="1",
        homework_slug=HOMEWORK_SLUGS[3],
    ),
    LocalQuestionSpec(
        text="Which practices are supported by an analytics engineering project?",
        question_type=QuestionTypes.CHECKBOXES.value,
        possible_answers=("Models", "Tests", "Documentation", "CSS styles"),
        correct_answer="1,2,3",
        scores_for_correct_answer=2,
        homework_slug=HOMEWORK_SLUGS[3],
    ),
    LocalQuestionSpec(
        text="What command runs the analytics engineering models?",
        question_type=QuestionTypes.FREE_FORM.value,
        correct_answer="dbt run",
        answer_type=AnswerTypes.EXACT_STRING.value,
        homework_slug=HOMEWORK_SLUGS[3],
    ),
    LocalQuestionSpec(
        text="How many rows are in the example model?",
        question_type=QuestionTypes.FREE_FORM.value,
        correct_answer="100",
        answer_type=AnswerTypes.INTEGER.value,
        homework_slug=HOMEWORK_SLUGS[3],
    ),
)

# Explicitly targeting an arbitrary homework remains useful in local development and
# preserves the original single-homework API contract.
LEGACY_FALLBACK_QUESTION_SPECS = tuple(LOCAL_QUESTION_SPECS[:3])


def assert_local_database() -> None:
    """Refuse to touch anything but a local/test SQLite database."""

    environment = getattr(settings, "RUNTIME_ENVIRONMENT", None)
    if environment not in ALLOWED_ENVIRONMENTS:
        raise LocalQuestionSeedError("environment-not-local")
    engine = settings.DATABASES.get("default", {}).get("ENGINE")
    if engine != SQLITE_ENGINE:
        raise LocalQuestionSeedError("database-not-local-sqlite")


def _validate_question_spec(spec: LocalQuestionSpec) -> None:
    if not spec.text.strip() or spec.scores_for_correct_answer <= 0:
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
            or (
                spec.question_type == QuestionTypes.MULTIPLE_CHOICE.value
                and len(correct_indices) != 1
            )
        ):
            raise LocalQuestionSeedError("question-spec-invalid")
        return

    if spec.question_type in FREE_FORM_QUESTION_TYPES:
        if (
            spec.possible_answers
            or not spec.correct_answer.strip()
            or spec.answer_type not in VALID_ANSWER_TYPES
        ):
            raise LocalQuestionSeedError("question-spec-invalid")
        return

    raise LocalQuestionSeedError("question-spec-invalid")


def validate_question_specs() -> None:
    """Validate every definition before a command can write any question."""

    if not LOCAL_QUESTION_SPECS or set(spec.homework_slug for spec in LOCAL_QUESTION_SPECS) != set(
        HOMEWORK_SLUGS
    ):
        raise LocalQuestionSeedError("question-spec-invalid")
    for spec in LOCAL_QUESTION_SPECS:
        _validate_question_spec(spec)


def _specs_for_homework(homework_slug: str) -> tuple[LocalQuestionSpec, ...]:
    specs = tuple(spec for spec in LOCAL_QUESTION_SPECS if spec.homework_slug == homework_slug)
    return specs or LEGACY_FALLBACK_QUESTION_SPECS


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


def check_all_local_question_seed(*, cohort_slug: str = DEFAULT_COHORT_SLUG) -> None:
    """Validate all local homework targets without writing."""

    assert_local_database()
    validate_question_specs()
    for homework_slug in HOMEWORK_SLUGS:
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
    specs = _specs_for_homework(homework.slug)

    with transaction.atomic():
        seeded = []
        for spec in specs:
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


def seed_all_local_questions(
    *,
    cohort_slug: str = DEFAULT_COHORT_SLUG,
) -> tuple[LocalQuestionSeedResult, ...]:
    """Create representative questions for local Homeworks 1 through 4."""

    check_all_local_question_seed(cohort_slug=cohort_slug)
    return tuple(
        seed_local_questions(cohort_slug=cohort_slug, homework_slug=homework_slug)
        for homework_slug in HOMEWORK_SLUGS
    )
