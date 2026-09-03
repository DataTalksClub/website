"""Real module titles and homework write-ups from a local course-repo checkout.

The graded exports only give us Google-Form-style question labels (already
used as ``Question.text`` -- see ``scoring_import.py``). The actual module
title and homework write-up live in the corresponding
``DataTalksClub/<course>-zoomcamp`` repository, under ``cohorts/<year>/``.
When a local checkout is present (e.g. ``~/git/data-engineering-zoomcamp``),
this module matches each imported homework to its real week/module folder --
by leading number, not position, since some editions skip a week -- and
returns its real title and homework.md content for ``scoring_import.py`` to
use instead of a generic placeholder. Entirely public course content: no
learner data involved.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .editions import HomeworkSource

COURSE_REPO_DIR_NAME = {
    "de-zoomcamp": "data-engineering-zoomcamp",
    "ml-zoomcamp": "machine-learning-zoomcamp",
    "mlops-zoomcamp": "mlops-zoomcamp",
}

_ACRONYMS = {"sql": "SQL", "gcp": "GCP", "aws": "AWS", "dbt": "dbt", "api": "API"}

_WEEK_FOLDER_RE = re.compile(r"^week_(\d+)(?:_|$)")
_MODULE_FOLDER_RE = re.compile(r"^(\d+)-")
_LEADING_NUMBER_RE = re.compile(r"^0*(\d+)")


@dataclass(frozen=True, slots=True)
class TopicContent:
    title: str
    instructions_markdown: str


def _humanize_tokens(tokens: list[str]) -> str:
    words = []
    for token in tokens:
        if not token:
            continue
        if token == "n":
            words.append("and")
            continue
        words.append(_ACRONYMS.get(token.lower(), token.capitalize()))
    return " ".join(words)


def _de_zoomcamp_folder(name: str) -> tuple[int, str] | None:
    match = _WEEK_FOLDER_RE.match(name)
    if not match:
        return None
    remainder = name[match.end():]
    topic = _humanize_tokens(remainder.split("_")) if remainder else ""
    label = f"Week {match.group(1)}" + (f": {topic}" if topic else "")
    return int(match.group(1)), label


def _module_folder(name: str) -> tuple[int, str] | None:
    match = _MODULE_FOLDER_RE.match(name)
    if not match:
        return None
    remainder = name[match.end():]
    topic = _humanize_tokens(remainder.split("-")) if remainder else ""
    label = f"Module {int(match.group(1))}" + (f": {topic}" if topic else "")
    return int(match.group(1)), label


_FOLDER_PARSER = {
    "de-zoomcamp": _de_zoomcamp_folder,
    "ml-zoomcamp": _module_folder,
    "mlops-zoomcamp": _module_folder,
}


def _homework_markdown(folder: Path) -> str:
    for candidate in (
        folder / "homework.md",
        folder / "homework" / "homework.md",
        folder / "homework" / "README.md",
    ):
        if candidate.is_file():
            try:
                return candidate.read_text(encoding="utf-8", errors="ignore").strip()
            except OSError:
                return ""
    return ""


def _load_topics_by_number(course_repo_dir: Path, course_slug: str, year: int) -> dict[int, list[TopicContent]]:
    parser = _FOLDER_PARSER[course_slug]
    cohort_dir = course_repo_dir / "cohorts" / str(year)
    if not cohort_dir.is_dir():
        return {}

    topics_by_number: dict[int, list[TopicContent]] = defaultdict(list)
    for folder in sorted(cohort_dir.iterdir(), key=lambda path: path.name):
        if not folder.is_dir():
            continue
        parsed = parser(folder.name)
        if parsed is None:
            continue
        number, title = parsed
        topics_by_number[number].append(
            TopicContent(title=title, instructions_markdown=_homework_markdown(folder))
        )
    return topics_by_number


def _leading_number(slug_part: str) -> int | None:
    match = _LEADING_NUMBER_RE.match(slug_part)
    return int(match.group(1)) if match else None


def match_homeworks_to_topics(
    homeworks: tuple[HomeworkSource, ...],
    topics_by_number: dict[int, list[TopicContent]],
) -> dict[str, TopicContent]:
    """Match each homework to its real topic by leading number, not position.

    Some editions skip a numbered week entirely (e.g. de-zoomcamp-2022 has no
    week 4 folder even though ``hw-04.csv`` exists) and some split one week
    across multiple homeworks (e.g. de-zoomcamp-2023's ``01a``/``01b``).
    Matching by number and only zipping same-number groups keeps a skipped
    week from silently mislabeling the homework after it.
    """

    grouped: dict[int, list[HomeworkSource]] = defaultdict(list)
    for homework in homeworks:
        number = _leading_number(homework.slug_part)
        if number is not None:
            grouped[number].append(homework)

    matches: dict[str, TopicContent] = {}
    for number, group in grouped.items():
        topics = topics_by_number.get(number, [])
        for homework, topic in zip(sorted(group, key=lambda h: h.slug_part), topics):
            matches[homework.slug_part] = topic
    return matches


def load_homework_topics(
    course_repos_dir: Path,
    course_slug: str,
    year: int,
    homeworks: tuple[HomeworkSource, ...],
) -> dict[str, TopicContent]:
    """Best-effort: {} if the local course repo checkout isn't present."""

    repo_dir_name = COURSE_REPO_DIR_NAME.get(course_slug)
    if repo_dir_name is None:
        return {}
    course_repo_dir = course_repos_dir / repo_dir_name
    if not course_repo_dir.is_dir():
        return {}
    topics_by_number = _load_topics_by_number(course_repo_dir, course_slug, year)
    return match_homeworks_to_topics(homeworks, topics_by_number)


__all__ = ["TopicContent", "load_homework_topics", "match_homeworks_to_topics"]
