"""The module page's syllabus: what the import stored about a module, ready to draw.

Everything here comes from fields the curriculum import fills (the module
write-up parsed into structured fields, lesson summaries and durations, the
code link).  Nothing is parsed at render time, and every part is optional: a
module whose write-up has not been imported yet simply draws fewer sections.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from courses.models import SharedLesson


@dataclass
class SyllabusLesson:
    lesson: SharedLesson
    number: int
    url: str
    description: str = ""
    minutes: int | None = None
    is_read: bool = False


@dataclass
class SyllabusPart:
    title: str
    summary: str
    lessons: list[SyllabusLesson]

    @property
    def first(self) -> int:
        return self.lessons[0].number if self.lessons else 0

    @property
    def last(self) -> int:
        return self.lessons[-1].number if self.lessons else 0


@dataclass
class ModuleSyllabus:
    intro: str = ""
    outcomes: list[str] = field(default_factory=list)
    parts: list[SyllabusPart] = field(default_factory=list)
    recordings: list[dict] = field(default_factory=list)
    further: list[dict] = field(default_factory=list)
    notes: list[dict] = field(default_factory=list)
    code_url: str = ""
    total_minutes: int | None = None

    @property
    def has_extras(self) -> bool:
        return bool(self.recordings or self.further or self.notes or self.code_url)


def _first(obj, *names, default=None):
    for name in names:
        value = getattr(obj, name, None)
        if value not in (None, "", [], {}):
            return value
    return default


def _minutes(lesson) -> int | None:
    seconds = _first(lesson, "video_duration_seconds", "duration_seconds")
    if seconds:
        return max(1, round(int(seconds) / 60))
    minutes = _first(lesson, "duration_minutes")
    return int(minutes) if minutes else None


def _links(value) -> list[dict]:
    """``[{title, url}]`` from a stored list of dicts or ``[title, url]`` pairs."""

    links = []
    for item in value or []:
        if isinstance(item, dict):
            url = item.get("url") or item.get("href") or ""
            title = item.get("title") or item.get("label") or url
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            title, url = item
        else:
            continue
        if url:
            links.append({"title": title, "url": url})
    return links


def module_syllabus(module, lessons, *, lesson_url, read_ids=frozenset()) -> ModuleSyllabus:
    """Gather the module's imported write-up and its lessons into one syllabus."""

    rows = [
        SyllabusLesson(
            lesson=lesson,
            number=index,
            url=lesson_url(lesson),
            description=_first(lesson, "summary", default=""),
            minutes=_minutes(lesson),
            is_read=lesson.pk in read_ids,
        )
        for index, lesson in enumerate(lessons, start=1)
    ]
    by_slug = {row.lesson.slug: row for row in rows}
    parts: list[SyllabusPart] = []
    placed: set[str] = set()
    for part in _first(module, "overview_parts", "parts", default=[]) or []:
        if not isinstance(part, dict):
            continue
        # The import stores each part's lessons as {"slug", "title", "description"}.
        slugs: list[str] = []
        for item in part.get("lessons") or []:
            slug = item.get("slug") if isinstance(item, dict) else item
            if isinstance(slug, str) and slug in by_slug:
                slugs.append(slug)
        if not slugs:
            continue
        placed.update(slugs)
        parts.append(
            SyllabusPart(
                title=part.get("title") or part.get("heading") or "",
                summary=part.get("summary") or part.get("intro_markdown") or "",
                lessons=[by_slug[slug] for slug in slugs],
            )
        )
    # Lessons the write-up does not group still belong in the list, so the
    # parts stand only when they cover every lesson; otherwise one plain list.
    if not parts or len(placed) != len(rows):
        parts = [SyllabusPart(title="", summary="", lessons=rows)] if rows else []
    known = [row.minutes for row in rows if row.minutes]
    return ModuleSyllabus(
        intro=_first(module, "intro", "build_summary", "description", default=""),
        outcomes=[str(item) for item in (_first(module, "outcomes", default=[]) or [])],
        parts=parts,
        recordings=_links(_first(module, "workshop_recordings", "recordings")),
        further=_links(_first(module, "further_reading", "optional_links")),
        notes=_links(_first(module, "community_notes", "notes")),
        code_url=_first(module, "code_url", default=""),
        total_minutes=sum(known) if rows and len(known) == len(rows) else None,
    )
