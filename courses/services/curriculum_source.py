"""Immutable source graph accepted by the course curriculum import service."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal


@dataclass(frozen=True, slots=True)
class CourseSource:
    content_id: str
    slug: str
    title: str
    description: str
    description_source_path: str | None
    outcome: str
    repository_url: str
    docs_url: str
    faq_url: str
    hashtag: str
    published: bool
    source_path: str


@dataclass(frozen=True, slots=True)
class UnitSource:
    content_id: str
    slug: str
    title: str
    source_path: str
    markdown: str


@dataclass(frozen=True, slots=True)
class ModuleSource:
    content_id: str
    slug: str
    title: str
    source_path: str
    units: tuple[UnitSource, ...]


@dataclass(frozen=True, slots=True)
class HomeworkOptionSource:
    id: str
    label: str


type AnswerEnvelopeValue = str | int
type AnswerEnvelope = Mapping[str, AnswerEnvelopeValue]


@dataclass(frozen=True, slots=True)
class HomeworkQuestionSource:
    content_id: str
    id: str
    type: Literal["multiple_choice", "checkboxes", "free_form", "free_form_long"]
    prompt: str
    points: int
    options: tuple[HomeworkOptionSource, ...]
    answer_type: Literal["any", "float", "integer", "exact_string", "contains_string"] | None
    answer: AnswerEnvelope | None


@dataclass(frozen=True, slots=True)
class HomeworkFormSource:
    homework_url: bool
    time_spent_lectures: bool
    time_spent_homework: bool
    faq_contribution: bool
    learning_in_public_cap: int


@dataclass(frozen=True, slots=True)
class HomeworkSource:
    content_id: str
    slug: str
    title: str
    source_path: str
    instructions_source_path: str
    instructions_markdown: str
    due_at: datetime
    initial_state: Literal["closed", "open", "scored"]
    form: HomeworkFormSource
    questions: tuple[HomeworkQuestionSource, ...]


@dataclass(frozen=True, slots=True)
class ModuleFlowSource:
    module: ModuleSource
    homework: HomeworkSource


@dataclass(frozen=True, slots=True)
class ProjectFlowSource:
    slug: str


type CurriculumFlowSource = ModuleFlowSource | ProjectFlowSource


@dataclass(frozen=True, slots=True)
class CohortSource:
    identifier: str
    format: Literal["legacy", "modules"]
    source_path: str | None
    content_id: str | None
    course_slug: str
    legacy_slug: str | None
    year: int | None
    title: str | None
    description: str | None
    published: bool | None
    start_date: date | None
    end_date: date | None
    flow: tuple[CurriculumFlowSource, ...]
    is_implicit_legacy: bool


@dataclass(frozen=True, slots=True)
class CourseRepositorySource:
    schema_version: int
    parser_version: str
    commit_sha: str | None
    course: CourseSource
    cohorts: tuple[CohortSource, ...]
    modules: tuple[ModuleSource, ...]
    homeworks: tuple[HomeworkSource, ...]


__all__ = (
    "AnswerEnvelope",
    "CohortSource",
    "CourseRepositorySource",
    "CourseSource",
    "CurriculumFlowSource",
    "HomeworkFormSource",
    "HomeworkOptionSource",
    "HomeworkQuestionSource",
    "HomeworkSource",
    "ModuleFlowSource",
    "ModuleSource",
    "ProjectFlowSource",
    "UnitSource",
)
