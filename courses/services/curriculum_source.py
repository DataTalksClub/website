"""Immutable source graph accepted by the course curriculum import service."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal


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
class InstructorSource:
    """One course instructor named by the course repository."""

    name: str
    url: str


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
    # What learners build in the final project, for the family landing's
    # projects section. ``None`` keeps existing curated database copy when
    # the optional key is absent.
    project_summary: str | None = None
    # The course's instructors in display order, as the repository lists
    # them. ``None`` keeps the stored value when the optional key is absent.
    instructors: tuple[InstructorSource, ...] | None = None
    # Cost and weekly time, one short factual sentence, from course.yaml.
    # ``None`` keeps the stored value when the optional key is absent.
    weekly_commitment: str | None = None


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
    # Length of the ``video_url`` video in seconds, fetched from YouTube by
    # zoomcamp-ops ``scripts/lesson-metadata/fill_video_durations.py``.
    video_duration_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class UnitSource:
    content_id: str
    slug: str
    title: str
    source_path: str
    markdown: str
    metadata: LessonMetadata = LessonMetadata()


@dataclass(frozen=True, slots=True)
class OverviewLessonSource:
    """One lesson as the README index lists it."""

    slug: str
    title: str
    description: str


@dataclass(frozen=True, slots=True)
class OverviewPartSource:
    """One group of lessons; ``heading`` is empty for an unheaded list."""

    heading: str
    intro_markdown: str
    lessons: tuple[OverviewLessonSource, ...]


@dataclass(frozen=True, slots=True)
class OverviewLinkSource:
    """One link from a recordings or notes section.

    ``label`` is the item text around the link (``Part 1 – RAG``), ``title``
    the link text and ``url`` the target as written.
    """

    label: str
    title: str
    url: str


@dataclass(frozen=True, slots=True)
class OverviewSectionSource:
    """Any other README section, kept as Markdown under its heading."""

    heading: str
    markdown: str


@dataclass(frozen=True, slots=True)
class ModuleOverviewSource:
    intro_markdown: str
    parts: tuple[OverviewPartSource, ...]
    recordings: tuple[OverviewLinkSource, ...]
    notes: tuple[OverviewLinkSource, ...]
    sections: tuple[OverviewSectionSource, ...]
    # The ``Code:`` link target exactly as written (relative or absolute).
    code_target: str | None

    def lesson_descriptions(self) -> dict[str, str]:
        """Return ``{lesson slug: description}`` for every described lesson."""

        descriptions: dict[str, str] = {}
        for part in self.parts:
            for lesson in part.lessons:
                if lesson.description and lesson.slug not in descriptions:
                    descriptions[lesson.slug] = lesson.description
        return descriptions


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
    # The same README parsed into fields at import time (schema 2 only);
    # ``None`` when the module has no README.
    overview: ModuleOverviewSource | None = None


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
    ``source`` is ``None`` for a module the cohort teaches without homework.
    """

    module: str | None
    source: str | None


@dataclass(frozen=True, slots=True)
class CriteriaSetSource:
    """One reusable review criteria set kept next to a schema-2 cohort config.

    ``criteria`` is already normalized by community-base ``parse_criteria_set``.
    """

    slug: str
    title: str
    description: str
    criteria: tuple[dict[str, Any], ...]
    source_path: str


@dataclass(frozen=True, slots=True)
class ProjectSource:
    """One project attempt declared in a schema-2 cohort config.

    ``module`` is a root shared module slug the attempt is placed in, or
    ``None``; ``criteria_set`` names a criteria set by slug.

    ``placement_only`` marks an entry that only places an attempt the course
    management platform already owns (title, deadlines, peers and criteria
    come from the CMP export); the import then sets just its module.
    """

    slug: str
    title: str
    module: str | None
    description_markdown: str
    description_source_path: str | None
    submission_due: datetime | None
    review_due: datetime | None
    peers: int | None
    criteria_set: str | None
    placement_only: bool = False


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
    criteria_sets: tuple[CriteriaSetSource, ...] = ()
    projects: tuple[ProjectSource, ...] = ()


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
    "CriteriaSetSource",
    "CurriculumFlowSource",
    "HomeworkBindingSource",
    "HomeworkFormSource",
    "HomeworkOptionSource",
    "HomeworkQuestionSource",
    "HomeworkSource",
    "HomeworkSummarySource",
    "InstructorSource",
    "LessonCodeSource",
    "LessonMetadata",
    "ModuleFlowSource",
    "ModuleOverviewSource",
    "ModuleSource",
    "OverviewLessonSource",
    "OverviewLinkSource",
    "OverviewPartSource",
    "OverviewSectionSource",
    "ProjectFlowSource",
    "ProjectSource",
    "UnitSource",
)
