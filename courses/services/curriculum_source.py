"""Immutable source graph accepted by the course curriculum import service."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal


@dataclass(frozen=True, slots=True)
class CourseProgressionStepSource:
    """One repository-authored scene in the course learner journey."""

    heading: str
    description: str


@dataclass(frozen=True, slots=True)
class HomeworkSummarySource:
    """Course-level copy for a legacy homework-backed syllabus row."""

    slug: str
    summary: str


@dataclass(frozen=True, slots=True)
class CourseSource:
    content_id: str
    slug: str
    title: str
    # ``None`` means the repository published no ``SITE.md`` (schema 1) and no
    # inline ``description`` (schema 2 requires one). The importer leaves the
    # existing description untouched rather than blanking curated copy.
    description: str | None
    description_source_path: str | None
    # A repository may publish the learner's truthful starting state. ``None``
    # keeps existing curated database copy when the optional key is absent.
    starting_point: str | None
    # Repositories may author prerequisite copy directly in course.yaml.
    # ``None`` means this source contract does not own the stored field.
    prerequisites: str | None
    # Three course-specific scenes, in narrative order. ``None`` preserves
    # existing curated database content when an older manifest omits them.
    progression: tuple[CourseProgressionStepSource, ...] | None
    # Legacy families may still compose their public syllabus from existing
    # Homework rows. The repository owns their one-line summaries by slug.
    homework_summaries: tuple[HomeworkSummarySource, ...]
    outcome: str
    repository_url: str
    docs_url: str
    faq_url: str
    hashtag: str
    published: bool
    source_path: str
    # Schema-2 only: the identifier of the one cohort whose own cohort.yaml
    # declares curriculum: current -- validated against course.yaml:cohorts
    # and the cohorts actually discovered on disk. None for schema 1.
    current_cohort: str | None = None


@dataclass(frozen=True, slots=True)
class LessonCodeSource:
    """One source file exposed by a lesson's ``code`` frontmatter."""

    label: str
    source_path: str


@dataclass(frozen=True, slots=True)
class LessonMetadata:
    """Structured lesson metadata kept separate from rendered Markdown."""

    video_url: str | None = None
    code: tuple[LessonCodeSource, ...] = ()


@dataclass(frozen=True, slots=True)
class UnitSource:
    content_id: str
    slug: str
    title: str
    source_path: str
    markdown: str
    metadata: LessonMetadata = LessonMetadata()


@dataclass(frozen=True, slots=True)
class ModuleSource:
    content_id: str
    slug: str
    title: str
    summary: str
    source_path: str
    units: tuple[UnitSource, ...]
    # ``shared`` marks one root module of the one current graph (schema 2);
    # ``cohort`` is a schema-1 module owned by one cohort.
    scope: Literal["shared", "cohort"] = "cohort"
    # Optional module README index, imported as an overview/intro only.  It is
    # never a lesson and never a fallback for a missing lesson.
    overview_markdown: str | None = None


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
class HomeworkBindingSource:
    """One explicit cohort-to-homework mapping from a schema-2 manifest.

    ``module`` is the root shared module slug, or ``None`` for an archive
    homework that keeps operating without a shared-module placement.
    """

    module: str | None
    source: str


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
    # Schema-2 discriminators.  ``None`` marks a schema-1 cohort; the shared
    # projection must never infer them from years, folders, or slugs.
    delivery: Literal["live", "self_paced"] | None = None
    curriculum: Literal["current", "github_archive"] | None = None
    archive_notice_path: str | None = None
    homework_bindings: tuple[HomeworkBindingSource, ...] = ()


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
    "CourseProgressionStepSource",
    "CourseSource",
    "CurriculumFlowSource",
    "HomeworkBindingSource",
    "HomeworkFormSource",
    "HomeworkOptionSource",
    "HomeworkQuestionSource",
    "HomeworkSource",
    "HomeworkSummarySource",
    "LessonCodeSource",
    "LessonMetadata",
    "ModuleFlowSource",
    "ModuleSource",
    "ProjectFlowSource",
    "UnitSource",
)
